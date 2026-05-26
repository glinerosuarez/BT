INTERNSHIP_TITLE_PATTERNS = {
    "intern_title": r"\bintern(ship)?\b",
    "coop_title": r"\bco[\s-]?op\b",
}

INTERNSHIP_DESCRIPTION_PATTERNS = {
    "internship_program": r"\bintern(ship)?\s+(program|position|role|opportunity|opening|cohort)\b",
    "seasonal_internship": r"\b(summer|fall|spring|winter)\s+intern(ship)?\b",
    "coop_program": r"\bco[\s-]?op\s+(program|position|role|opportunity|opening)\b",
}

ML_DATA_KEYWORDS = {
    "machine learning": 3.0,
    "ml": 2.0,
    "data science": 3.0,
    "data scientist": 2.5,
    "data engineering": 2.5,
    "data engineer": 2.0,
    "analytics": 1.2,
    "statistical": 1.0,
    "python": 1.0,
    "sql": 1.0,
    "nlp": 2.0,
    "computer vision": 2.0,
    "llm": 2.0,
    "deep learning": 2.5,
    "tensorflow": 1.2,
    "pytorch": 1.2,
    "experimentation": 1.0,
    "a/b testing": 1.0,
}

US_LOCATION_HINTS = {
    "united states",
    "usa",
    "us",
    "u.s.",
}

NEGATIVE_WORK_AUTH_PATTERNS = {
    "must_authorized_us": r"\bmust be authorized to work in the (us|u\.s\.|united states)\b",
    "authorized_us_required": r"\bauthorized to work in the (us|u\.s\.|united states)\b",
    "requires_us_work_auth": r"\brequires?\s+(current\s+)?(us|u\.s\.|united states)\s+work authorization\b",
    "must_have_us_work_auth": r"\bmust have\s+(current\s+)?(us|u\.s\.|united states)\s+work authorization\b",
    "citizen_or_pr_required": r"\b(us citizens?\s+only|must be a us citizen|must be (a )?permanent resident)\b",
}

POSITIVE_SPONSORSHIP_PATTERNS = {
    "visa_sponsorship": r"\bvisa sponsorship\b",
    "sponsorship_available": r"\bsponsorship available\b",
    "cpt": r"\bcpt\b",
    "opt": r"\bopt\b",
    "international_students": r"\binternational students\b",
    "h1b": r"\bh-?1b\b",
    "willing_to_sponsor": r"\bwilling to sponsor\b",
    "open_to_sponsorship": r"\bopen to sponsorship\b",
}
