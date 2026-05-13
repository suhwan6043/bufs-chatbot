# Tailscale 팀 개발 환경 설정 가이드

팀원 Windows 서버(Docker Compose 운영) + 사용자 Mac 개발 환경을 **같은 사설 네트워크처럼** 묶는 설정서.
공인 IP·포트포워딩 없이 `http://bufs-server:80` 같은 호스트명으로 팀원 서버에 접근 가능.

- **비용**: 무료 (Tailscale 개인 플랜: 3사용자·100디바이스)
- **보안**: 트래픽은 WireGuard로 피어 간 직통 암호화. Tailscale 회사는 트래픽 못 봄.
- **OS**: 팀원=Windows(서버 관리·Docker 운영), 사용자=macOS(개발)

---

## 사전 준비 (둘 중 한 명이 한 번만)

1. https://login.tailscale.com/start 접속
2. Google / GitHub / Microsoft 계정 중 **팀 공용으로 쓸 수 있는 하나** 선택해 회원가입
   - 개인 계정이라도 상관없음. 단 이 계정이 tailnet 소유자가 됨.
3. 가입 완료 후 admin 콘솔 URL 저장: https://login.tailscale.com/admin/machines
4. 다른 사람 초대: admin 콘솔 → `Users` → `Invite user` → 이메일 입력 또는 공유 링크 복사
   - 초대받은 쪽은 링크 클릭 → 같은 tailnet에 자동 합류

> 2인 팀이면 **한 계정 공유**도 간단하지만, 각자 별도 계정으로 초대받는 편이 권한 관리·로그 구분에 유리.

---

## 팀원 (Windows + Docker) — 서버 쪽 설정

### 1단계: Tailscale 설치

1. https://tailscale.com/download/windows 에서 MSI 다운로드
2. 설치 마법사 완료 후 자동으로 시스템 트레이에 아이콘 등장
3. 트레이 아이콘 우클릭 → `Log in...` → 위에서 만든(또는 초대받은) 계정으로 OAuth 로그인
4. 로그인 후 트레이 메뉴에서 `This device: <PC이름> (100.x.x.x)` 확인

### 2단계: 호스트명 지정 (선택이지만 강력 권장)

admin 콘솔(https://login.tailscale.com/admin/machines) 접속 → 방금 등록된 Windows 머신 → `Edit machine name` → `bufs-server` 로 변경.
이후 MagicDNS로 `bufs-server` 이름만으로 접근 가능.

### 3단계: Tailscale SSH 활성화 (선택, 편의 기능)

PowerShell **관리자 권한**:
```powershell
tailscale up --ssh
```

활성화하면 별도 SSH 키 관리 없이 사용자 Mac에서 `ssh <windows사용자명>@bufs-server`로 바로 접속 가능. 신원은 Tailscale 로그인 계정으로 인증.

### 4단계: Ollama 호스트 바인딩 변경 (중요)

Windows Ollama는 기본적으로 `127.0.0.1:11434`만 응답. tailnet에서 접근하려면 모든 인터페이스에 바인딩해야 함.

PowerShell **관리자 권한**:
```powershell
# 시스템 환경변수 설정 (영구)
setx OLLAMA_HOST "0.0.0.0:11434" /M

# Ollama 서비스 재시작
Restart-Service -Name Ollama
# 서비스가 아니면 작업 관리자에서 ollama.exe 종료 후 재실행, 또는 재부팅
```

확인:
```powershell
netstat -an | findstr 11434
# → 0.0.0.0:11434  LISTENING  이 떠야 OK
```

> `127.0.0.1:11434 LISTENING`만 보이면 환경변수가 Ollama 프로세스에 적용 안 된 것. 재부팅 또는 Ollama GUI 종료→재시작.

### 5단계: Windows Defender 방화벽 룰 추가

Docker Desktop이 관리하는 포트(80, 3000, 8000)는 보통 자동 허용되지만, Ollama는 Windows 호스트에서 직접 돌기 때문에 **수동 허용 필요**.

PowerShell **관리자 권한**:
```powershell
# Ollama (tailnet 인터페이스에서만 허용 → 공용 인터넷엔 닫힘)
New-NetFirewallRule -DisplayName "Ollama (Tailscale)" `
  -Direction Inbound -Protocol TCP -LocalPort 11434 `
  -InterfaceAlias "Tailscale" -Action Allow

# nginx (Docker가 publish한 80) — Docker Desktop이 이미 허용했을 수 있지만 명시 권장
New-NetFirewallRule -DisplayName "BUFS nginx (Tailscale)" `
  -Direction Inbound -Protocol TCP -LocalPort 80 `
  -InterfaceAlias "Tailscale" -Action Allow

# 직접 접근이 필요한 경우 — 백엔드 8000, 프론트 3000
New-NetFirewallRule -DisplayName "BUFS Backend (Tailscale)" `
  -Direction Inbound -Protocol TCP -LocalPort 8000 `
  -InterfaceAlias "Tailscale" -Action Allow
New-NetFirewallRule -DisplayName "BUFS Frontend (Tailscale)" `
  -Direction Inbound -Protocol TCP -LocalPort 3000 `
  -InterfaceAlias "Tailscale" -Action Allow
```

`-InterfaceAlias "Tailscale"` 지정으로 **tailnet 트래픽만 허용**. 공용 인터넷은 여전히 차단.

### 6단계: Docker Compose 재기동 (필요 시)

Ollama URL이 이미 `host.docker.internal:11434`로 잡혀 있어 변경 불필요. 백엔드 재시작만:
```powershell
cd C:\path\to\bufs-chatbot\docker
docker compose down
docker compose up -d
```

CORS 허용이 필요하면 [docker/docker-compose.yml](docker/docker-compose.yml) `CORS_ORIGINS`에 `http://bufs-server,http://bufs-server:3000` 추가.

### 7단계: 상시 실행 확인

- **Tailscale**: 설치 시 Windows 서비스로 등록 → 재부팅 후 자동 실행 (기본 OK)
- **Ollama**: 서비스로 등록되어 자동 시작 (기본 OK)
- **Docker Desktop**: 설정 → `Start Docker Desktop when you log in` 체크
- **Docker Compose 스택**: `restart: unless-stopped`가 compose 파일에 있어 Docker 시작 시 자동 기동

### 8단계: 팀원 쪽 확인 (자체 테스트)

```powershell
# 자기 tailnet IP 확인
tailscale ip -4
# 예: 100.64.12.34

# 서비스 응답 확인 (로컬)
curl http://localhost:11434/api/tags
curl http://localhost/api/health   # nginx 경유 백엔드
```

둘 다 정상 응답이면 서버 준비 완료. **사용자에게 tailnet 접속 가능하다고 알림.**

---

## 사용자 (Mac) — 개발 쪽 설정

### 1단계: Tailscale 설치

```bash
# Homebrew 권장 (CLI 친화적)
brew install --cask tailscale

# 설치 후 첫 실행
open /Applications/Tailscale.app
```

또는 Mac App Store에서 "Tailscale" 검색 설치.

### 2단계: 로그인

메뉴바의 Tailscale 아이콘 클릭 → `Log in` → **팀원과 같은 계정**(또는 초대받은 계정)으로 OAuth 로그인.

### 3단계: 연결 확인

터미널:
```bash
# tailnet 피어 목록
tailscale status
# → bufs-server (팀원 Windows)가 보여야 함

# 팀원 서버에 핑
ping bufs-server
# → 100.x.x.x 응답

# 팀원 Ollama 확인
curl http://bufs-server:11434/api/tags
# → {"models": [...]} JSON 응답

# 팀원 백엔드 확인 (nginx 경유)
curl http://bufs-server/api/health
# → {"status": "ok", ...}
```

위 네 명령이 전부 성공하면 원격 개발 환경 준비 완료.

### 4단계: 개발 시나리오별 환경 설정

#### 시나리오 A: 프론트엔드만 로컬 개발, 백엔드는 팀원 서버

```bash
cd frontend
# .env.local 생성
cat > .env.local <<EOF
NEXT_PUBLIC_API_URL=http://bufs-server
EOF

npm install
npm run dev
# → http://localhost:3000 (Mac 브라우저) — API 호출은 팀원 백엔드로
```

#### 시나리오 B: 백엔드 로컬 실행, LLM만 팀원 GPU 사용

Mac에서 평가·실험 돌릴 때 유용. 팀원 남는 VRAM(~10GB)을 공유 LLM으로 활용.

```bash
# 프로젝트 루트 .env.local (또는 .env 직접 수정)
cat >> .env.local <<EOF
LLM_BASE_URL=http://bufs-server:11434
LLM_MODEL=gemma3:12b-q6_K
EOF

# 평가 실행 — LLM 호출만 원격, 검색·리랭킹은 Mac에서
python scripts/eval_contains_f1.py --dataset data/eval/...
```

#### 시나리오 C: 팀원 백엔드에 바로 붙어 전체 동작 확인

```bash
# 브라우저에서 직접 접속
open http://bufs-server
```

### 5단계: 원격 쉘 접속 (선택)

팀원 서버 로그를 보거나 컨테이너 상태 확인하려면:

```bash
# 팀원이 3단계에서 tailscale up --ssh 했다면
ssh <windows사용자명>@bufs-server

# Windows 쉘에서 Docker 상태 확인
docker ps
docker logs camchat-backend --tail 100
```

또는 Tailscale 앱 메뉴에서 `Services` → 원격 호스트 선택으로 GUI 접속도 가능.

### 6단계: 데이터 동기화 (필요할 때)

`data/chromadb/`, `data/graphs/`, `data/crawl_meta/` 같은 인덱스 파일은 git 대상 아님. 팀원 서버가 크롤링·인제스트하면 결과물을 가져와야 Mac 로컬 평가가 최신 상태로 돌아감.

#### 옵션 1: rsync over SSH (권장, 증분 동기)

팀원이 [Windows OpenSSH Server](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)를 활성화했거나 Tailscale SSH를 켰다면:

```bash
# 인덱스·그래프·크롤메타만 선택 동기
rsync -av --delete \
  <windows사용자명>@bufs-server:/path/to/bufs-chatbot/data/chromadb/ \
  ./data/chromadb/

rsync -av \
  <windows사용자명>@bufs-server:/path/to/bufs-chatbot/data/graphs/ \
  ./data/graphs/
```

#### 옵션 2: Taildrop (간단, 수동)

팀원 쪽에서:
```powershell
tailscale file cp "C:\path\to\data-snapshot.tar.gz" 사용자-mac:
```

사용자 쪽:
```bash
tailscale file get ~/Downloads
tar -xzf ~/Downloads/data-snapshot.tar.gz -C ./data/
```

주 1회 스냅샷 공유 수준엔 충분.

#### 옵션 3: SMB 공유 (실시간 읽기, 대용량 평가엔 비추)

팀원이 Windows에서 `data/` 폴더 우클릭 → `속성` → `공유` → 특정 사용자 권한 부여.
Mac Finder에서 `⌘K` → `smb://bufs-server/bufs-chatbot-data` 마운트.

---

## 양쪽 연결 동작 확인 (최종 체크리스트)

| 확인 항목 | 명령 | 기대 결과 |
|-----------|------|-----------|
| Tailscale 피어 인식 | Mac에서 `tailscale status` | `bufs-server`가 online으로 표시 |
| 네트워크 도달 | Mac에서 `ping bufs-server` | 응답 있음 |
| Ollama 접근 | Mac에서 `curl http://bufs-server:11434/api/tags` | 모델 JSON 반환 |
| 백엔드 헬스체크 | Mac에서 `curl http://bufs-server/api/health` | `{"status":"ok"}` |
| 브라우저 접근 | `open http://bufs-server` | 챗봇 UI 로드 |
| 프론트 로컬 개발 | Mac `npm run dev` → `localhost:3000`에서 채팅 | 답변 정상 스트리밍 |

---

## 트러블슈팅

### 팀원 쪽

- **`curl http://localhost:11434` 는 되는데 Mac에서 `curl http://bufs-server:11434` 실패**
  → `OLLAMA_HOST=0.0.0.0:11434` 적용 안 됨. `netstat -an | findstr 11434`로 `0.0.0.0` 바인딩 확인. 안 되어 있으면 재부팅.
- **방화벽 룰 적용 안 됨**
  → `Get-NetFirewallRule | Where-Object DisplayName -Match "Tailscale"`로 목록 확인. `-InterfaceAlias`가 `Tailscale`로 설정됐는지 체크.
- **Docker Compose 컨테이너에서 `host.docker.internal` 해석 안 됨**
  → Docker Desktop 사용 중이면 자동 지원. Docker Engine on Windows(드문 경우)는 `extra_hosts`로 수동 매핑 필요.

### 사용자 쪽

- **`bufs-server` 해석 실패 (`ping: cannot resolve`)**
  → MagicDNS 비활성화 상태. admin 콘솔 → `DNS` → `MagicDNS` 토글 On. 또는 `tailscale ip -4`로 뽑은 `100.x.x.x` IP를 직접 사용.
- **연결 처음엔 되다가 느려지거나 끊김**
  → 피어 간 직통 연결(DERP 릴레이 안 탐)이 맺혔는지 확인: `tailscale status` 출력에서 해당 피어 라인에 `direct`가 보이면 OK, `relay "..."`면 릴레이 경유(대역폭 제한). NAT 타입이 symmetric이면 릴레이만 가능 — 대부분 문제 없음.
- **`rsync: command not found` (팀원 Windows 쪽)**
  → Windows엔 기본 rsync 없음. 팀원이 Git Bash 또는 WSL2 설치하면 사용 가능. 대안: Taildrop 또는 SMB.

### 공통

- **Tailscale 연결은 되는데 특정 포트만 접근 안 됨**
  → Windows 방화벽 룰 누락. 5단계 룰 추가.
- **계정 공유 보안 걱정**
  → 각자 별도 계정으로 초대. 2FA 필수 활성화 (GitHub/Google 2FA가 자동으로 Tailscale에도 적용됨).
- **Tailscale 쓰기 싫어지면**
  → `tailscale down` (일시 정지) / `tailscale logout` (완전 해제). 설정·데이터엔 영향 없음.

---

## 롤아웃 권장 순서

1. **팀원**: 1~4단계(설치·호스트명·SSH·Ollama 바인딩) → 8단계로 자체 확인
2. **팀원**: 5~7단계(방화벽·Docker 재기동·상시 실행)
3. **사용자**: 1~3단계(설치·로그인·연결 확인)
4. **사용자**: 4단계 중 시나리오 A (프론트 로컬 개발)부터 검증
5. **양쪽**: 최종 체크리스트 6개 항목 통과 확인
6. **사용자**: 필요에 따라 6단계(데이터 동기화) 설정 — 첫 평가 돌리기 전에
