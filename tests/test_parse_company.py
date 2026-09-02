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


def test_parse_smartrecruiters_postings_reads_sections_and_structured_location():
    from parse_company import parse_smartrecruiters_postings
    payload = {"jobs": [
        {"name": "Software Engineer Intern",
         "postingUrl": "https://jobs.smartrecruiters.com/Acme/1",
         "location": {"city": "Austin", "region": "TX", "country": "us"},
         "jobAd": {"sections": {
             "jobDescription": {"text": "<p>Join us for Summer 2027.</p>"},
             "qualifications": {"text": "<p>Pursuing a BS or MS.</p>"}}},
         "releasedDate": "2026-08-06T15:02:42.506Z"},
        {"name": "Hardware Intern",  # no 2027 evidence in any section
         "postingUrl": "https://jobs.smartrecruiters.com/Acme/2",
         "location": {"city": "Austin", "region": "TX", "country": "us"},
         "jobAd": {"sections": {"jobDescription": {"text": "Fall 2026 start"}}}},
        {"name": "Data Intern",  # passed the fetch filter, but has no city/region
         "postingUrl": "https://jobs.smartrecruiters.com/Acme/3",
         "location": {"country": "us"},
         "jobAd": {"sections": {"jobDescription": {"text": "Summer 2027"}}}},
    ]}
    postings, drops = parse_smartrecruiters_postings(payload, BOARD_SOURCE)

    assert len(postings) == 1
    assert postings[0]["role"] == "Software Engineer Intern"
    assert postings[0]["location"] == "Austin, TX"
    assert postings[0]["degree"] == ["BS", "MS"]
    assert postings[0]["date_posted"] == "2026-08-06"
    assert postings[0]["link"] == "https://jobs.smartrecruiters.com/Acme/1"
    assert drops == {"term_unmatched": 1, "non_us_location": 1}


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


def test_workday_place_reshapes_the_tenant_specific_location_formats():
    from parse_company import _workday_place
    # Shapes seen across the 114-board watch-list. The reshaper only ever
    # hands a candidate to canonicalize_location; it never decides US-ness.
    assert _workday_place("Houston, Texas, United States of America", True) == "Houston, TX"
    assert _workday_place("United States-California-Palmdale", True) == "Palmdale, CA"
    assert _workday_place("Kissimmee, FL (Celebration Blvd)", True) == "Kissimmee, FL"
    assert _workday_place("Orlando, FL (Maitland, FL)", True) == "Orlando, FL"
    assert _workday_place("US-CT-EAST HARTFORD-ETC ~ 400 Main St ~ BLDG ETC",
                          True) == "East Hartford, CT"
    assert _workday_place("US-TX-PLANO-465 ~ 465 Independence Pkwy ~ INDEPENDENCE",
                          True) == "Plano, TX"
    # Only the trailing site code is dropped, so a hyphenated city survives.
    assert _workday_place("US-NC-WINSTON-SALEM-123 ~ 1 Main St", True) == "Winston-Salem, NC"


def test_workday_place_still_drops_what_is_not_confidently_us():
    from parse_company import _workday_place
    # Canada must never be reshaped into a US state, and a bare city has no
    # state to recover — both stay dropped.
    assert _workday_place("Toronto, ON", False) is None
    assert _workday_place("Milton, Ontario", False) is None
    assert _workday_place("CA-ON-TORONTO-1 ~ 1 King St", False) is None
    assert _workday_place("Waukesha", True) is None
    assert _workday_place("United States", True) is None
    assert _workday_place("", True) is None
    # A US-country job may still list a non-US site; the canonicalizer, not
    # the country flag, decides each place.
    assert _workday_place("Bengaluru, India", True) is None


def test_parse_workday_search_recovers_a_tenant_specific_location():
    from parse_company import parse_workday_search
    payload = {"jobs": [{
        "title": "Electrical Engineering Intern (Summer 2027)",
        "externalUrl": "https://globalhr.wd5.myworkdayjobs.com/s/job/x_R1",
        "location": "US-TX-MCKINNEY-513WZ ~ 2501 W University Dr ~ WING Z BLDG",
        "additionalLocations": ["US-MA-TEWKSBURY-TB1 ~ 50 Apple Hill Dr ~ ASSABET BLDG"],
        "country": {"descriptor": "United States of America"},
        "jobDescription": "Summer 2027"}]}
    postings, drops = parse_workday_search(payload, BOARD_SOURCE)

    assert postings[0]["location"] == "Mckinney, TX / Tewksbury, MA"
    assert drops == {}


def test_parse_workday_search_rejects_a_drifted_payload():
    from parse_company import parse_workday_search
    import pytest
    with pytest.raises(ValueError):
        parse_workday_search({"jobPostings": []}, BOARD_SOURCE)


def test_parse_workable_jobs_applies_intern_term_and_us_rules():
    from parse_company import parse_workable_jobs
    src = {"company": "Acme", "source_entity": "company:acme"}
    us = {"countryCode": "US", "city": "Austin", "region": "Texas"}
    jobs = [
        {"title": "Software Engineer Intern", "description": "<p>Summer 2027 internship</p>",
         "requirements": "<p>Pursuing a BS or MS</p>", "location": us, "published": "2026-08-30T00:00:00.000Z",
         "link": "https://apply.workable.com/acme/j/AAA/"},
        {"title": "ML Intern", "description": "Summer 2027", "location": {"countryCode": "US"},
         "remote": True, "workplace": "remote", "link": "https://apply.workable.com/acme/j/BBB/"},
        {"title": "Data Intern", "description": "Summer 2027", "location": {"countryCode": "CA", "city": "Toronto", "region": "Ontario"},
         "link": "https://apply.workable.com/acme/j/CCC/"},
        {"title": "Platform Intern", "description": "Fall 2026 co-op", "location": us,
         "link": "https://apply.workable.com/acme/j/DDD/"},
        {"title": "Senior Engineer", "description": "Summer 2027", "location": us,
         "link": "https://apply.workable.com/acme/j/EEE/"},
    ]
    postings, drops = parse_workable_jobs({"jobs": jobs}, src)
    assert [(p["role"], p["location"], p["degree"], p.get("date_posted")) for p in postings] == [
        ("Software Engineer Intern", "Austin, TX", ["BS", "MS"], "2026-08-30"),
        ("ML Intern", "Remote (US)", ["BS"], None),
    ]
    assert dict(drops) == {"non_us_location": 1, "term_unmatched": 1, "role_unmatched": 1}
