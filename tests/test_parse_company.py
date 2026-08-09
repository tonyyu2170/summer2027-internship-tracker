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


BOARD_SOURCE = {"company": "Acme", "source_entity": "company:acme"}


def test_parse_greenhouse_board_filters_and_maps():
    from parse_company import parse_greenhouse_board
    payload = {"jobs": [
        {"title": "Software Engineer Intern",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
         "location": {"name": "New York, NY"},
         "content": "Join us for Summer 2027. BS or MS students welcome.",
         "first_published": "2026-08-01T00:00:00-04:00"},
        {"title": "Software Engineer",  # not an intern role
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
         "location": {"name": "New York, NY"}, "content": "Summer 2027"},
        {"title": "Data Intern",  # wrong cycle, no 2027 evidence
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/3",
         "location": {"name": "New York, NY"}, "content": "Fall 2026 start"},
        {"title": "ML Intern",  # non-US
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/4",
         "location": {"name": "London"}, "content": "Summer 2027"},
    ]}
    postings, drops = parse_greenhouse_board(payload, BOARD_SOURCE)
    assert len(postings) == 1
    p = postings[0]
    assert p["role"] == "Software Engineer Intern"
    assert p["term"] == "Summer 2027"
    assert p["degree"] == ["BS", "MS"]
    assert p["date_posted"] == "2026-08-01"
    assert p["source"] == "company:acme"
    assert drops == {"role_unmatched": 1, "term_unmatched": 1, "non_us_location": 1}


def test_parse_lever_postings_maps_epoch_date():
    from parse_company import parse_lever_postings
    payload = [
        {"text": "Quantitative Research Intern",
         "hostedUrl": "https://jobs.lever.co/acme/abc",
         "categories": {"location": "Chicago, IL"},
         "descriptionPlain": "PhD internship, Summer 2027.",
         "createdAt": 1786000000000},
        {"text": "Office Manager",
         "hostedUrl": "https://jobs.lever.co/acme/def",
         "categories": {"location": "Chicago, IL"},
         "descriptionPlain": "Summer 2027"},
    ]
    postings, drops = parse_lever_postings(payload, BOARD_SOURCE)
    assert len(postings) == 1
    assert postings[0]["degree"] == ["PhD"]
    assert postings[0]["date_posted"].startswith("2026-08")
    assert drops == {"role_unmatched": 1}


def test_parse_ashby_board_rules():
    from parse_company import parse_ashby_board
    payload = {"jobs": [
        {"title": "Hardware Engineer Intern", "isListed": True,
         "location": "Austin, TX", "jobUrl": "https://jobs.ashbyhq.com/acme/1",
         "descriptionHtml": "<p>Summer 2027 cohort. Bachelor's required.</p>",
         "publishedAt": "2026-08-02T12:00:00Z"},
        {"title": "Research Resident", "isListed": True,  # employmentType signal
         "employmentType": "Intern", "location": "Remote (US)",
         "jobUrl": "https://jobs.ashbyhq.com/acme/2",
         "descriptionHtml": "<p>2027 Summer program</p>"},
        {"title": "SWE Intern", "isListed": False,
         "location": "Austin, TX", "jobUrl": "https://jobs.ashbyhq.com/acme/3",
         "descriptionHtml": "Summer 2027"},
        {"title": "SWE Intern", "isListed": True,  # no 2027 evidence
         "location": "Austin, TX", "jobUrl": "https://jobs.ashbyhq.com/acme/4",
         "descriptionHtml": "<p>Join anytime</p>"},
    ]}
    postings, drops = parse_ashby_board(payload, BOARD_SOURCE)
    assert [p["role"] for p in postings] == ["Hardware Engineer Intern", "Research Resident"]
    assert postings[0]["date_posted"] == "2026-08-02"
    assert drops == {"unlisted": 1, "term_unmatched": 1}


def test_parse_workday_search_uses_detail_fields():
    from parse_company import parse_workday_search
    payload = {"jobs": [
        # Title carries no "Summer 2027"; the description supplies the evidence,
        # and additionalLocations resolves what search showed as "3 Locations".
        {"title": "2027 Intern Software Engineer",
         "externalUrl": "https://ngc.wd1.myworkdayjobs.com/site/job/McLean-VA/x_R1",
         "location": "McLean, VA",
         "additionalLocations": ["San Francisco,  CA", "Bengaluru, India"],
         "startDate": "2026-08-03", "postedOn": "Posted 6 Days Ago",
         "jobDescription": "<p>Summer 2027 cohort. Master's students.</p>"},
        {"title": "Finance Intern",  # no 2027 evidence anywhere
         "externalUrl": "https://ngc.wd1.myworkdayjobs.com/site/job/McLean-VA/y_R2",
         "location": "McLean, VA", "startDate": "2026-08-03",
         "jobDescription": "Ongoing program"},
        {"title": "Staff Engineer",  # not an intern role
         "externalUrl": "https://ngc.wd1.myworkdayjobs.com/site/job/McLean-VA/z_R3",
         "location": "McLean, VA", "jobDescription": "Summer 2027"},
        {"title": "Intern, Strategy - Summer 2027",  # non-US
         "externalUrl": "https://ngc.wd1.myworkdayjobs.com/site/job/Toronto-ON/w_R4",
         "location": "Toronto, ON", "jobDescription": "Summer 2027"},
    ]}
    postings, drops = parse_workday_search(payload, BOARD_SOURCE)

    assert len(postings) == 1
    p = postings[0]
    assert p["role"] == "2027 Intern Software Engineer"
    assert p["location"] == "McLean, VA / San Francisco, CA"
    assert p["link"] == "https://ngc.wd1.myworkdayjobs.com/site/job/McLean-VA/x_R1"
    assert p["term"] == "Summer 2027"
    assert p["degree"] == ["MS"]
    assert p["date_posted"] == "2026-08-03"
    assert p["source"] == "company:acme"
    assert drops == {"term_unmatched": 1, "role_unmatched": 1, "non_us_location": 1}


def test_parse_workday_search_omits_date_without_a_start_date():
    from parse_company import parse_workday_search
    payload = {"jobs": [
        {"title": "SWE Intern", "location": "Austin, TX",
         "externalUrl": "https://acme.wd1.myworkdayjobs.com/s/job/Austin-TX/a_R9",
         "jobDescription": "Summer 2027"},
        {"title": "SWE Intern", "location": "Austin, TX",  # unusable link
         "jobDescription": "Summer 2027"},
    ]}
    postings, drops = parse_workday_search(payload, BOARD_SOURCE)

    assert "date_posted" not in postings[0]
    assert drops == {"malformed_posting": 1}


def test_parse_workday_search_rejects_a_drifted_payload():
    from parse_company import parse_workday_search
    import pytest
    with pytest.raises(ValueError):
        parse_workday_search({"jobPostings": []}, BOARD_SOURCE)
