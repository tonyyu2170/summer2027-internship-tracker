from link_check import (
    workday_cxs_url,
    is_greenhouse_dead_redirect,
    classify_status_code,
    classify_link,
)


def test_workday_cxs_url_transforms_job_url():
    link = "https://leidos.wd5.myworkdayjobs.com/External/job/Chantilly-VA/Software-Engineer-Intern_R-00183707"
    assert workday_cxs_url(link) == (
        "https://leidos.wd5.myworkdayjobs.com/wday/cxs/leidos/External/"
        "job/Chantilly-VA/Software-Engineer-Intern_R-00183707"
    )


def test_workday_cxs_url_returns_none_for_non_workday_link():
    assert workday_cxs_url("https://boards.greenhouse.io/foo/jobs/123") is None


def test_is_greenhouse_dead_redirect_true_when_redirected_to_board_root():
    original = "https://job-boards.greenhouse.io/podium81/jobs/7939921"
    final = "https://job-boards.greenhouse.io/podium81?error=true"
    assert is_greenhouse_dead_redirect(original, final) is True


def test_is_greenhouse_dead_redirect_false_when_job_path_survives():
    original = "https://job-boards.greenhouse.io/podium81/jobs/7939921"
    final = "https://job-boards.greenhouse.io/podium81/jobs/7939921"
    assert is_greenhouse_dead_redirect(original, final) is False


def test_classify_status_code():
    assert classify_status_code(200) == "alive"
    assert classify_status_code(404) == "dead"
    assert classify_status_code(410) == "dead"
    assert classify_status_code(403) == "unknown"
    assert classify_status_code(429) == "unknown"
    assert classify_status_code(500) == "unknown"


def test_classify_link_probes_workday_cxs_endpoint_and_detects_dead():
    link = "https://leidos.wd5.myworkdayjobs.com/External/job/Chantilly-VA/Software-Engineer-Intern_R-00183707"
    cxs = workday_cxs_url(link)

    def probe(url):
        assert url == cxs
        return (404, url)

    assert classify_link(link, probe) == "dead"


def test_classify_link_workday_alive():
    link = "https://leidos.wd5.myworkdayjobs.com/External/job/Chantilly-VA/Software-Engineer-Intern_R-00183707"

    def probe(url):
        return (200, url)

    assert classify_link(link, probe) == "alive"


def test_classify_link_detects_greenhouse_dead_redirect():
    link = "https://job-boards.greenhouse.io/podium81/jobs/7939921"

    def probe(url):
        return (200, "https://job-boards.greenhouse.io/podium81?error=true")

    assert classify_link(link, probe) == "dead"


def test_classify_link_generic_404_is_dead():
    link = "https://www.workatastartup.com/jobs/94993"

    def probe(url):
        return (404, url)

    assert classify_link(link, probe) == "dead"


def test_classify_link_generic_403_is_unknown():
    link = "https://www.citadel.com/careers/details/x/"

    def probe(url):
        return (403, url)

    assert classify_link(link, probe) == "unknown"
