from parse_company import parse_phenom_job_page, parse_workday_cxs


SOURCE = {
    "company": "Oliver Wyman",
    "provider": "phenom_job_page",
    "url": "https://careers.example.com/job/R_356561/actuarial-intern",
    "source_entity": "company:marsh-oliver-wyman",
    "term": "Summer 2027",
    "degree": ["BS"],
    "role_pattern": r"(?i)actuar",
}

WORKDAY_SOURCE = {
    "company": "Genworth Financial",
    "provider": "workday_cxs",
    "url": "https://gnw.wd1.myworkdayjobs.com/Genworth_Confidential",
    "source_entity": "company:genworth",
    "term": "Summer 2027",
    "degree": ["BS"],
    "role_pattern": r"(?i)actuar",
    "term_pattern": r"(?i)summer\s+2027",
    "tenant": "gnw",
    "site": "Genworth_Confidential",
    "search_text": "actuarial",
}


def test_parse_phenom_job_page_uses_jobposting_data_and_configured_fields():
    html = '''
    <script type="application/ld+json">{
      "@context": "https://schema.org", "@type": "JobPosting",
      "title": "Oliver Wyman Actuarial - Internship - Summer 2027",
      "datePosted": "2026-07-26",
      "jobLocation": [
        {"@type": "Place", "address": {
          "addressLocality": "New York", "addressRegion": "New York"}},
        {"@type": "Place", "address": {
          "addressLocality": "Boston", "addressRegion": "MA"}}
      ]
    }</script>'''

    postings = parse_phenom_job_page(html, SOURCE)

    assert postings == [{
        "company": "Oliver Wyman",
        "role": "Oliver Wyman Actuarial - Internship - Summer 2027",
        "location": "New York, NY / Boston, MA",
        "link": SOURCE["url"],
        "term": "Summer 2027",
        "degree": ["BS"],
        "source": "company:marsh-oliver-wyman",
        "date_posted": "2026-07-26",
    }]


def test_parse_phenom_job_page_returns_empty_for_an_unmatched_role():
    html = '''<script type="application/ld+json">{
      "@type": "JobPosting", "title": "Claims Internship",
      "jobLocation": {"address": {
        "addressLocality": "Hartford", "addressRegion": "CT"}}
    }</script>'''

    assert parse_phenom_job_page(html, SOURCE) == []


def test_parse_phenom_job_page_rejects_non_us_or_missing_locations():
    html = '''<script type="application/ld+json">{
      "@type": "JobPosting", "title": "Actuarial Internship",
      "jobLocation": {"address": {
        "addressLocality": "London", "addressRegion": "England"}}
    }</script>'''

    try:
        parse_phenom_job_page(html, SOURCE)
    except ValueError as exc:
        assert "US location" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_parse_workday_cxs_filters_to_summer_2027_and_builds_canonical_links():
    payload = {"jobPostings": [
        {
            "title": "Genworth Actuarial Development Program Analyst – 2027",
            "externalPath": "/job/Richmond-Virginia/analyst_REQ-260275",
            "locationsText": "Richmond, Virginia",
        },
        {
            "title": "Genworth Actuarial Development Program Intern – Summer 2027",
            "externalPath": "/job/Richmond-Virginia/intern_REQ-260272",
            "locationsText": "Richmond, Virginia",
        },
    ]}

    postings, drops = parse_workday_cxs(payload, WORKDAY_SOURCE)

    assert postings == [{
        "company": "Genworth Financial",
        "role": "Genworth Actuarial Development Program Intern – Summer 2027",
        "location": "Richmond, VA",
        "link": "https://gnw.wd1.myworkdayjobs.com/Genworth_Confidential/job/Richmond-Virginia/intern_REQ-260272",
        "term": "Summer 2027",
        "degree": ["BS"],
        "source": "company:genworth",
    }]
    assert drops == {"term_unmatched": 1}


def test_parse_workday_cxs_counts_malformed_and_non_us_results():
    payload = {"jobPostings": [
        {"title": "Actuarial Intern – Summer 2027", "externalPath": "/job/london", "locationsText": "London, England"},
        {"title": "Actuarial Intern – Summer 2027"},
    ]}

    postings, drops = parse_workday_cxs(payload, WORKDAY_SOURCE)

    assert postings == []
    assert drops == {"non_us_location": 1, "malformed_posting": 1}
