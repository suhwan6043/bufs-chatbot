"use client";
import { useState, use } from "react";
import { useRouter } from "next/navigation";
import type { Lang } from "@/lib/types";
import { t } from "@/lib/i18n";
import { useSession } from "@/hooks/useSession";

const YEARS = Array.from({ length: 12 }, (_, i) => 2026 - i);
const STUDENT_TYPES = ["type_domestic", "type_intl", "type_transfer"] as const;
const STYPE_MAP: Record<string, string> = { type_domestic: "내국인", type_intl: "외국인", type_transfer: "편입생" };

// ─────────────────────────────────────────────────────────────────────────────
// 학과/전공 목록 (ko = 파이프라인 저장값 / en = 어휘집 번역값)
// ─────────────────────────────────────────────────────────────────────────────
type DeptOption = { ko: string; en: string };
type CollegeGroup = { ko: string; en: string; depts: DeptOption[] };

const COLLEGES: CollegeGroup[] = [
  {
    ko: "유럽미주대학",
    en: "College of European and American Studies",
    depts: [
      { ko: "영어학부",            en: "Department of English" },
      { ko: "영어전공",            en: "English Major" },
      { ko: "영어통번역전공",      en: "English Interpretation and Translation Major" },
      { ko: "유럽학부",            en: "Department of European Studies" },
      { ko: "프랑스어전공",        en: "French Language Major" },
      { ko: "독일어전공",          en: "German Language Major" },
      { ko: "스페인어전공",        en: "Spanish Language Major" },
      { ko: "포르투갈(브라질)어전공", en: "Portuguese Language Major" },
      { ko: "이탈리아어전공",      en: "Italian Language Major" },
      { ko: "러시아어전공",        en: "Russian Language Major" },
      { ko: "유럽지역통상전공",    en: "European Regional Trade Major" },
    ],
  },
  {
    ko: "아시아대학",
    en: "College of Asian Studies",
    depts: [
      { ko: "일본어융합학부",          en: "Department of Japanese Studies" },
      { ko: "한일문화콘텐츠전공",      en: "Korean-Japanese Cultural Content Major" },
      { ko: "비즈니스일본어전공",      en: "Business Japanese Major" },
      { ko: "일본어IT전공",            en: "Japanese IT Major" },
      { ko: "중국학부",                en: "Department of Chinese Studies" },
      { ko: "중국어전공",              en: "Chinese Language Major" },
      { ko: "중국지역통상전공",        en: "Chinese Regional Trade Major" },
      { ko: "태국어전공",              en: "Thai Language Major" },
      { ko: "인도네시아·말레이시아전공", en: "Indonesian and Malay Language Major" },
      { ko: "베트남어전공",            en: "Vietnamese Language Major" },
      { ko: "미얀마어전공",            en: "Burmese Language Major" },
      { ko: "인도어전공",              en: "Hindi Language Major" },
      { ko: "아랍어전공",              en: "Arabic Language Major" },
      { ko: "튀르키예어전공",          en: "Turkish Language Major" },
    ],
  },
  {
    ko: "사회과학대학",
    en: "College of Social Sciences",
    depts: [
      { ko: "사회복지전공",        en: "Social Welfare Major" },
      { ko: "상담심리전공",        en: "Counseling Psychology Major" },
      { ko: "경찰행정전공",        en: "Police Administration Major" },
      { ko: "사이버경찰전공",      en: "Cyber Police Major" },
      { ko: "한국어교육전공",      en: "Korean Language Education Major" },
      { ko: "외교전공",            en: "Diplomacy Major" },
      { ko: "국제개발협력전공",    en: "International Development Cooperation Major" },
      { ko: "글로벌인재융합전공",  en: "Global Talent Convergence Major" },
      { ko: "글로벌미래융합학부",  en: "Global Future Convergence School" },
      { ko: "스포츠재활전공",      en: "Sports Rehabilitation Major" },
      { ko: "사회체육전공",        en: "Community Sports Major" },
      { ko: "시민영어교육학과",    en: "English Education for Citizens" },
      { ko: "글로벌문화비즈니스전공", en: "Global Culture Business Major" },
    ],
  },
  {
    ko: "상경대학",
    en: "College of Business and Economics",
    depts: [
      { ko: "경영전공",          en: "Business Administration Major" },
      { ko: "회계전공",          en: "Accounting Major" },
      { ko: "경제금융전공",      en: "Economics and Finance Major" },
      { ko: "국제사무전공",      en: "International Affairs and Secretary Major" },
      { ko: "국제마케팅전공",    en: "International Marketing Major" },
      { ko: "국제무역전공",      en: "International Trade Major" },
      { ko: "국제문화관광전공",  en: "International Cultural Tourism Major" },
      { ko: "호텔·컨벤션전공",  en: "Hotel and Convention Major" },
      { ko: "항공서비스전공",    en: "Airline Service Major" },
      { ko: "글로벌창업융합전공", en: "Global Startup Convergence Major" },
    ],
  },
  {
    ko: "디지털미디어·IT대학",
    en: "College of Digital Media and IT",
    depts: [
      { ko: "컴퓨터공학전공",        en: "Computer Engineering Major" },
      { ko: "소프트웨어전공",        en: "Software Engineering Major" },
      { ko: "스마트융합보안전공",    en: "Smart Convergence Security Major" },
      { ko: "전자·인공지능융합전공", en: "Electronics and AI Convergence Major" },
      { ko: "빅데이터전공",          en: "Big Data Major" },
      { ko: "글로벌웹툰콘텐츠전공", en: "Global Webtoon Content Major" },
      { ko: "영상콘텐츠융합전공",    en: "Video Content Convergence Major" },
      { ko: "스마트에너지·환경전공", en: "Smart Energy and Environment Major" },
    ],
  },
  {
    ko: "글로벌자유전공학부",
    en: "Global Liberal Major School",
    depts: [
      { ko: "글로벌자유전공학부", en: "Global Liberal Major School" },
    ],
  },
  {
    ko: "만오교양대학",
    en: "Liberal Arts College",
    depts: [
      { ko: "만오교양대학 교학팀",  en: "Liberal Arts College Office" },
      { ko: "융합교양교육센터",     en: "Convergence Liberal Arts Education Center" },
    ],
  },
  {
    ko: "International College",
    en: "International College",
    depts: [
      { ko: "International College", en: "International College" },
    ],
  },
];

export default function OnboardingPage({ params }: { params: Promise<{ lang: string }> }) {
  const { lang: rawLang } = use(params);
  const lang = (rawLang === "en" ? "en" : "ko") as Lang;
  const router = useRouter();
  const { sessionId, updateProfile } = useSession(lang);

  const [year, setYear] = useState(2023);
  const [dept, setDept] = useState("");
  const [stype, setStype] = useState("type_domestic");
  const [agreed, setAgreed] = useState(false);
  const [warn, setWarn] = useState("");

  const handleStart = async () => {
    if (!agreed) { setWarn(t(lang, "onboard.warn_agree")); return; }
    await updateProfile({
      student_id: String(year),
      department: dept || "",
      student_type: STYPE_MAP[stype] || "내국인",
    });
    router.push(`/${lang}/chat`);
  };

  const handleSkip = () => {
    router.push(`/${lang}/chat`);
  };

  return (
    <div className="min-h-screen bg-main flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-6">
          <div className="text-5xl mb-2">{"\uD83C\uDF93"}</div>
          <h1 className="text-xl font-bold text-navy">{t(lang, "onboard.welcome")}</h1>
          <p className="text-sm text-text-sub mt-1">{t(lang, "onboard.desc")}</p>
        </div>

        {/* Form */}
        <div className="bg-white rounded-xl border border-border p-6 shadow-sm space-y-4">
          {/* Year */}
          <div>
            <label className="block text-sm font-medium text-text mb-1">{"\uD83D\uDCC5"} {t(lang, "onboard.year_label")}</label>
            <select value={year} onChange={(e) => setYear(Number(e.target.value))}
              className="w-full px-3 py-2 border border-border rounded-lg text-sm focus:border-accent outline-none">
              {YEARS.map((y) => (
                <option key={y} value={y}>{t(lang, "onboard.year_fmt", { y })}</option>
              ))}
            </select>
          </div>

          {/* Department — grouped dropdown, EN labels from en_glossary */}
          <div>
            <label className="block text-sm font-medium text-text mb-1">{"\uD83C\uDFEB"} {t(lang, "onboard.dept_label")}</label>
            <select
              value={dept}
              onChange={(e) => setDept(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-lg text-sm focus:border-accent outline-none bg-white"
            >
              <option value="">{t(lang, "onboard.dept_none")}</option>
              {COLLEGES.map((college) => (
                <optgroup
                  key={college.ko}
                  label={lang === "en" ? college.en : college.ko}
                >
                  {college.depts.map((d) => (
                    <option key={d.ko} value={d.ko}>
                      {lang === "en" ? d.en : d.ko}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          {/* Student type */}
          <div>
            <label className="block text-sm font-medium text-text mb-1">{"\uD83D\uDC64"} {t(lang, "onboard.type_label")}</label>
            <div className="flex gap-2">
              {STUDENT_TYPES.map((st) => (
                <button key={st} onClick={() => setStype(st)}
                  className={`flex-1 py-2 rounded-lg text-sm font-medium border transition-all ${
                    stype === st ? "bg-accent text-white border-accent" : "bg-white text-text-sub border-border hover:border-accent"
                  }`}>
                  {t(lang, `onboard.${st}`)}
                </button>
              ))}
            </div>
          </div>

          {/* Disclaimer */}
          <div className="bg-amber-50 border border-amber-300 rounded-lg p-3">
            <p className="text-xs text-amber-800 leading-relaxed">{t(lang, "onboard.disclaimer")}</p>
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={agreed} onChange={(e) => { setAgreed(e.target.checked); setWarn(""); }}
              className="w-4 h-4 rounded border-border text-accent" />
            <span className="text-sm text-text">{t(lang, "onboard.agree")}</span>
          </label>
          {warn && <p className="text-xs text-red-500">{warn}</p>}

          {/* Buttons */}
          <div className="flex gap-3 pt-2">
            <button onClick={handleStart}
              className="flex-[3] py-2.5 bg-accent text-white rounded-lg font-medium hover:bg-accent/90 transition-colors">
              {t(lang, "onboard.btn_start")}
            </button>
            <button onClick={handleSkip}
              className="flex-[2] py-2.5 bg-white text-text-sub border border-border rounded-lg font-medium hover:bg-gray-50 transition-colors">
              {t(lang, "onboard.btn_skip")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
