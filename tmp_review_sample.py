"""Throwaway sample so the Claude review workflow has a diff to look at.
This whole file is deleted with the tmp/claude-review-test branch."""


def add_numbers(a, b):
    result = a + b
    return result


def divide(a, b):
    # intentionally naive — gives the reviewer something to comment on
    return a / b


if __name__ == "__main__":
    print(add_numbers(2, 3))
    print(divide(10, 0))
