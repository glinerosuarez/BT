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

HIGH_SIGNAL_ML_DATA_KEYWORDS = {
    "machine learning",
    "data science",
    "data scientist",
    "data engineering",
    "data engineer",
    "ml engineer",
    "applied scientist",
    "research scientist",
    "computer vision",
    "nlp",
    "deep learning",
    "pytorch",
    "tensorflow",
    "llm",
    "sql",
}

DATA_ROLE_TITLE_PATTERNS = {
    "machine_learning_title": r"\b(machine learning|ml)\b",
    "ai_engineering_title": r"\bai\s+engineer(ing)?\b",
    "data_science_title": r"\bdata (science|scientist)\b",
    "data_engineer_title": r"\bdata engineer(ing)?\b",
    "analytics_engineer_title": r"\banalytics engineer\b",
    "applied_research_title": r"\b(applied|research) scientist\b",
    "quant_title": r"\bquant(itative)?\b",
}

BACKEND_ADJACENT_TITLE_PATTERNS = {
    "software_engineer_intern": r"\bsoftware (development|engineer(?:ing)?)\b.*\bintern(ship)?\b|\bintern(ship)?\b.*\bsoftware (development|engineer(?:ing)?)\b",
    "backend_engineer_intern": r"\bbackend\b.*\bintern(ship)?\b|\bintern(ship)?\b.*\bbackend\b",
    "platform_engineer_intern": r"\bplatform engineer\b.*\bintern(ship)?\b|\bintern(ship)?\b.*\bplatform engineer\b",
}

BACKEND_ADJACENT_DESCRIPTION_PATTERNS = {
    "backend": r"\bbackend systems?\b|\bbackend\b",
    "api": r"\bapis?\b|\brest APIs?\b",
    "distributed_systems": r"\bdistributed services?\b|\bdistributed systems?\b",
    "databases": r"\b(relational|non-relational) databases?\b|\bdatabases?\b",
    "messaging": r"\b(kafka|rabbitmq|redis|pub/sub|messaging|queuing systems?)\b",
    "containers": r"\b(docker|kubernetes)\b",
    "cloud": r"\bcloud-based solutions?\b|\bcloud\b",
    "scalability": r"\bscal(e|able|ability)\b|\bhigh-transaction\b|\breal-time\b",
}

NON_DATA_ROLE_TITLE_PATTERNS = {
    "developer_advocacy": r"\bdeveloper advocacy\b",
    "go_to_market": r"\bgo[- ]to[- ]market\b",
    "content_role": r"\b(content|video content|editorial)\b",
    "sales_marketing": r"\b(sales|marketing|partnerships?)\b",
    "customer_success": r"\bcustomer success\b",
    "recruiting_ops": r"\b(recruit(er|ing)|talent|hr|human resources)\b",
    "frontend_mobile_only": r"\b(frontend|front-end|ios|android|mobile app|react native)\b",
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
    "us_work_auth_required": r"\b(current\s+)?(us|u\.s\.|united states)\s+work authorization\s+required\b",
    "must_have_us_work_auth": r"\bmust have\s+(current\s+)?(us|u\.s\.|united states)\s+work authorization\b",
    "us_work_authorized_only": r"\b(indefinite\s+)?(us|u\.s\.|united states)\s+work authorized individuals only\b",
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
