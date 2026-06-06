"""Throwaway sample to verify the Claude PR-review workflow posts a review.
This whole file is deleted with the test-tmp branch — do not merge."""


def add_numbers(a, b):
    return a + b


def divide(a, b):
    # intentionally naive (no zero-division guard) to give the reviewer something to flag
    return a / b


if __name__ == "__main__":
    print(add_numbers(2, 3))
    print(divide(10, 0))
