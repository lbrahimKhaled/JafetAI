import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@mail\.aub\.edu")
ID_RE = re.compile(r"[0-9]{6,12}")  # ascii digits only, real AUB IDs are 9
PROGRAMS = ("Undergraduate", "Graduate")


def student_problems(student_name, student_id, email, program):
    """Everything wrong with the student details LibCal needs, as a list of
    human-readable problems. Empty list means the details are usable.

    This is the deterministic backstop: the booking tools never reach LibCal
    with bad details, whatever the model decided to pass."""
    problems = []
    if not str(student_name or "").strip():
        problems.append("student_name is required")
    if not ID_RE.fullmatch(str(student_id or "")):
        problems.append("student_id must be 6-12 ASCII digits")
    if not EMAIL_RE.fullmatch(str(email or "")):
        problems.append("email must be an AUB address ending in @mail.aub.edu")
    if program not in PROGRAMS:
        problems.append("program must be Undergraduate or Graduate")
    return problems
