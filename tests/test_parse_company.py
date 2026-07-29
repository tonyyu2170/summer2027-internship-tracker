from parse_company import parse_phenom_job_page


SOURCE = {
    "company": "Oliver Wyman",
    "provider": "phenom_job_page",
    "url": "https://careers.example.com/job/R_356561/actuarial-intern",
    "source_entity": "company:marsh-oliver-wyman",
    "term": "Summer 2027",
    "degree": ["BS"],
    "role_pattern": r"(?i)actuar",
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
