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

FULL_TIME_BACKEND_ADJACENT_TITLE_PATTERNS = {
    "software_engineer": r"\b(software\s+(development|engineer(?:ing)?)|swe|sde)\b",
    "backend_engineer": r"\bbackend(\s+(software\s+)?engineer(?:ing)?)?\b",
    "platform_engineer": r"\bplatform(\s+(software\s+)?engineer(?:ing)?)?\b",
    "systems_engineer": r"\bsystems?(\s+(software\s+)?engineer(?:ing)?)?\b",
    "infrastructure_engineer": r"\binfrastructure(\s+(software\s+)?engineer(?:ing)?)?\b",
    "full_stack_engineer": r"\bfull[- ]?stack(\s+(software\s+)?engineer(?:ing)?)?\b",
    "forward_deployed_engineer": r"\b(forward\s+deploy(ed|ment)?|deploy(ed|ment)?\s+forward)\b.*?\bengineer(?:ing)?\b|\bengineer(?:ing)?\b.*?\b(forward\s+deploy(ed|ment)?|deploy(ed|ment)?\s+forward)\b|\bfde\b",
}

MANAGEMENT_TITLE_PATTERNS = {
    "manager": r"\b(engineering\s+manager|manager|tech\s+lead\s+manager|software\s+manager|em)\b",
    "director": r"\b(director|head\s+of)\b",
    "executive": r"\b(vp|vice\s+president|chief|cto|cio|ciso)\b",
    "staff": r"\bstaff\b",
    "principal": r"\bprincipal\b",
    "distinguished": r"\bdistinguished\b",
    "fellow": r"\btechnical\s+fellow\b",
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

US_LOCATION_PATTERN = r"(?:\b(?:united states|usa|us)\b|\bu\.s\.(?:a\.)?)"

NON_US_LOCATION_PATTERN = (
    r"\b(?:"
    r"germany|deutschland|dusseldorf|düsseldorf|berlin|munich|münchen|frankfurt|hamburg|stuttgart|cologne|köln|"
    r"united kingdom|great britain|england|scotland|wales|\buk\b|"
    r"canada|toronto|vancouver|montreal|montréal|ottawa|calgary|edmonton|quebec|alberta|"
    r"india|bangalore|bengaluru|hyderabad|mumbai|new delhi|pune|chennai|noida|gurugram|gurgaon|kolkata|"
    r"united arab emirates|\buae\b|dubai|abu dhabi|"
    r"saudi arabia|riyadh|"
    r"australia|sydney|melbourne|brisbane|perth|adelaide|"
    r"switzerland|suisse|schweiz|geneva|genève|zug|zurich|zürich|basel|lausanne|"
    r"netherlands|amsterdam|rotterdam|utrecht|the hague|eindhoven|"
    r"spain|españa|madrid|barcelona|valencia|seville|"
    r"france|paris|lyon|marseille|toulouse|"
    r"belgium|belgique|belgien|brussels|bruxelles|"
    r"ireland|dublin|cork|galway|"
    r"poland|polska|warsaw|warszawa|krakow|kraków|wroclaw|wrocław|"
    r"singapore|"
    r"japan|tokyo|osaka|kyoto|"
    r"china|beijing|shanghai|shenzhen|hangzhou|"
    r"brazil|brasil|sao paulo|são paulo|rio de janeiro|"
    r"(?<!new\s)(?:mexico|méxico|guadalajara|monterrey)|"
    r"italy|italia|rome|roma|milan|milano|"
    r"sweden|sverige|stockholm|gothenburg|"
    r"denmark|danmark|copenhagen|"
    r"norway|norge|oslo|"
    r"finland|suomi|helsinki|"
    r"israel|tel aviv|jerusalem|haifa|"
    r"taiwan|taipei|"
    r"south korea|korea|seoul|"
    r"emea|apac|latam|europe|asia"
    r")\b"
)

US_MAJOR_CITIES_PATTERN = (
    r"\b(?:austin|seattle|san francisco|san jose|new york|chicago|boston|los angeles|denver|atlanta|dallas|houston)\b"
)

NEGATIVE_WORK_AUTH_PATTERNS = {
    "us_work_authorized_only": r"\b(indefinite\s+)?(us|u\.s\.|united states)\s+work authorized individuals only\b",
    "citizen_or_pr_required": (
        r"\b(?:u\.?s\.?|united states)\s+citizens?\s+only\b|"
        r"\bmust be (?:a )?(?:u\.?s\.?|united states) citizen\b|"
        r"\b(?:u\.?s\.?|united states)\s+citizenship\s+(?:is\s+)?required\b|"
        r"\bmust be (a )?permanent resident\b"
    ),
    "citizens_only_security_clearance": (
        r"\bonly\s+(?:u\.?s\.?|united states)\s+citizens?\s+are\s+eligible\s+for\s+"
        r"(?:a\s+)?(?:government(?:-issued)?\s+)?security clearance\b"
    ),
    "itar_us_person_required": (
        r"\bitar requirements?\b(?s:.{0,700}?)"
        r"\b(?:u\.?s\.?\s+citizen|u\.?s\.?\s+lawful,?\s+permanent resident|green card holder)\b"
    ),
}

# CPT can satisfy these statements, so retain them as review signals rather
# than treating them as evidence that an F-1 student is categorically ineligible.
AMBIGUOUS_WORK_AUTH_PATTERNS = {
    "must_authorized_us": r"\bmust be authorized to work in the (us|u\.s\.|united states)\b",
    "authorized_us_required": r"\bauthorized to work in the (us|u\.s\.|united states)\b",
    "requires_us_work_auth": r"\brequires?\s+(current\s+)?(us|u\.s\.|united states)\s+work authorization\b",
    "us_work_auth_required": r"\b(current\s+)?(us|u\.s\.|united states)\s+work authorization\s+required\b",
    "must_have_us_work_auth": r"\bmust have\s+(current\s+)?(us|u\.s\.|united states)\s+work authorization\b",
}

POSITIVE_SPONSORSHIP_PATTERNS = {
    "visa_sponsorship": r"\b(?:visa|immigration) sponsorship\b",
    "sponsorship_available": r"\bsponsorship available\b",
    "cpt": r"\bcpt\b",
    "opt": r"\bopt\b",
    "international_students": r"\binternational students\b",
    "h1b": r"\bh-?1b\b",
    "willing_to_sponsor": r"\bwilling to sponsor\b",
    "open_to_sponsorship": r"\bopen to sponsorship\b",
}
