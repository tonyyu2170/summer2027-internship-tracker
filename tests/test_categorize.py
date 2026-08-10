import yaml

from categorize import (
    classify_role,
    map_upstream_category,
    assign_category,
    known_link_locations,
    manual_link_categories,
)


def test_hardware_wins_over_quant_for_quant_firm_hardware_roles():
    # Regression guard for 0fdf5dd: Akuna Capital Hardware Engineer was
    # miscategorized as quant. Hardware must be checked before quant.
    assert classify_role("Hardware Engineer Intern") == "hardware"
    assert classify_role("Hardware Engineer (FPGA/ASIC) Intern") == "hardware"
    assert classify_role("Quantitative Hardware Engineer") == "hardware"


def test_classify_role_basic_categories():
    assert classify_role("Quantitative Trader Intern") == "quant"
    assert classify_role("Quantitative Research Intern (PHD)") == "quant"
    assert classify_role("Machine Learning Engineer Intern") == "ai_ml"
    assert classify_role("Data Scientist Intern") == "data_science"
    assert classify_role("Actuarial Intern") == "actuarial"
    assert classify_role("Investment Banking Summer Analyst") == "__drop__"
    assert classify_role("Consulting Intern") == "__drop__"
    assert classify_role("Software Engineer Intern") == "swe"


def test_classify_role_routes_non_software_disciplines_to_hardware():
    # swe's bare `engineer` used to claim all of these.
    assert classify_role("Mechanical Engineering Intern (Summer 2027)") == "hardware"
    assert classify_role("Intern, Industrial Engineering") == "hardware"
    assert classify_role("WED - Intern Civil Engineer (Summer 2027)") == "hardware"
    assert classify_role("Chemical Engineering Intern") == "hardware"
    assert classify_role("Electrical Engineering Intern") == "hardware"
    assert classify_role(
        "Raytheon Electrical Engineering Intern (Summer 2027)(Onsite)") == "hardware"
    # The discipline has to modify "engineer": a bare-word match would
    # misroute this genuinely-software role.
    assert classify_role(
        "Privacy and Civil Liberties Software Engineer Intern") == "swe"
    # In-scope specialties still win over the discipline rule.
    assert classify_role("Mechanical Engineer, Data Analytics Intern") == "data_science"


def test_classify_role_returns_none_when_no_rule_matches():
    assert classify_role("Summer Intern") is None
    assert classify_role("Business Intern") is None


def test_map_upstream_category_handles_both_ai_data_spellings():
    # simplifyjobs/suryaharikrishnan spell it "AI/ML/Data"; zshah101 uses
    # "Data & ML/AI". Both split on role text.
    assert map_upstream_category("AI/ML/Data", "Data Scientist Intern") == "data_science"
    assert map_upstream_category("AI/ML/Data", "ML Engineer Intern") == "ai_ml"
    assert map_upstream_category("Data & ML/AI", "Data Analyst Intern") == "data_science"
    assert map_upstream_category("Data & ML/AI", "Deep Learning Intern") == "ai_ml"


def test_map_upstream_category_known_values():
    assert map_upstream_category("Software", "X") == "swe"
    assert map_upstream_category("Software Engineering", "X") == "swe"
    assert map_upstream_category("Quant", "X") == "quant"
    assert map_upstream_category("Quantitative Finance", "X") == "quant"
    assert map_upstream_category("Hardware", "X") == "hardware"
    assert map_upstream_category("Consulting", "Consulting Intern") == "__drop__"
    assert map_upstream_category("Investment Banking", "Summer Analyst") == "__drop__"


def test_map_upstream_category_drops_product():
    assert map_upstream_category("Product", "Product Manager Intern") == "__drop__"


def test_map_upstream_category_unknown_value_falls_through_to_classifier():
    # Upstream repos rename categories without notice. Unknown values must
    # never be dropped — they fall through to role-text classification.
    assert map_upstream_category("Cybersecurity", "Software Engineer Intern") == "swe"
    assert map_upstream_category("Brand New Bucket", "Summer Intern") is None


def test_assign_category_never_reclassifies_a_known_link():
    # merge_category dedupes within one category file only, so a link that
    # moves category would exist twice with nothing to catch it.
    known = {"https://example.com/jobs/1": "quant"}
    posting = {"link": "https://example.com/jobs/1?utm_source=x",
               "role": "Hardware Engineer Intern"}
    assert assign_category(posting, known) == "quant"


def test_assign_category_uses_rules_for_unknown_link():
    assert assign_category(
        {"link": "https://example.com/jobs/2", "role": "Software Engineer Intern"},
        {},
    ) == "swe"


def test_assign_category_returns_none_when_undecidable():
    assert assign_category(
        {"link": "https://example.com/jobs/3", "role": "Summer Intern"}, {}
    ) is None


def test_hardware_asic_requires_word_boundary():
    # "asic" must not substring-match inside "Basic" — the un-bounded
    # alternative previously miscategorized this as hardware.
    assert classify_role("Basic Data Entry Intern") is None


def test_quant_does_not_match_quantity():
    # "\bquant" (no trailing boundary) previously prefix-matched "Quantity",
    # miscategorizing a role that has nothing to do with quant finance.
    assert classify_role("Quantity Surveyor Intern") is None


def test_classify_role_drops_recurring_nontarget_families():
    # Added 2026-08-09 with the scheduled scrape: rows classify_role leaves
    # as None re-block every unattended merge, so recurring non-target
    # families (observed in zapplyjobs/chieler) must resolve to DROP.
    assert classify_role("Product Manager Intern") == "__drop__"
    assert classify_role("Supply Chain Intern") == "__drop__"
    assert classify_role("Human Resources Intern") == "__drop__"
    assert classify_role("HR Recruiting Intern") == "__drop__"
    assert classify_role("Accounting Internship - Summer 2027") == "__drop__"
    assert classify_role("Finance Intern - Year-Round Rotation Program") == "__drop__"
    assert classify_role("Brand Marketing Intern") == "__drop__"
    assert classify_role("Quality & Manufacturing Intern (Summer 2027)") == "__drop__"
    assert classify_role("Graphic Design Intern — NYC") == "__drop__"
    assert classify_role("Biologics Formulation Research Intern") == "__drop__"
    assert classify_role("Compliance Analyst Co-Op") == "__drop__"
    assert classify_role("Insights Intern - Multiple Teams") == "__drop__"
    assert classify_role("Financial Analyst Internship - Summer 2027") == "__drop__"
    assert classify_role("Communications and Publicity Internship") == "__drop__"
    assert classify_role("Summer 2027 Key Investment Services Intern") == "__drop__"
    assert classify_role("CAD/RMS System Administrator - Intern") == "__drop__"
    assert classify_role("Radiology Tech Intern / Casual") == "__drop__"
    assert classify_role("Chemist (Co-Op - Santa Clara)") == "__drop__"
    assert classify_role("Skillbridge Internship -IO") == "__drop__"
    assert classify_role("New Grad Returning Planner I- Summer 2027") == "__drop__"
    assert classify_role("Summer 2027 KeyBank Wealth Management Intern") == "__drop__"
    assert classify_role("Boeing Summer 2027 Internship Program...") == "__drop__"
    assert classify_role("Intern") == "__drop__"
    # CVS/Walgreens post pharmacy interns in bulk every run.
    assert classify_role("Pharmacy Intern") == "__drop__"
    assert classify_role("Pharmacy Intern - Grad") == "__drop__"


def test_drop_families_checked_last_so_in_scope_keywords_win():
    # The out-of-scope rule sits after every in-scope rule, so a title
    # carrying any in-scope keyword must never fall into it.
    assert classify_role("Supply Chain Software Engineer Intern") == "swe"
    assert classify_role("Quantitative Finance Intern") == "quant"
    assert classify_role("Machine Learning Operations Intern") == "ai_ml"
    assert classify_role("Manufacturing Data Analyst Intern") == "data_science"
    assert classify_role("Pharmaceutical Data Science Intern") == "data_science"


def test_new_in_scope_families():
    assert classify_role("Business Intelligence Intern") == "data_science"
    assert classify_role("Analog Design Intern - Master's Degree") == "hardware"
    assert classify_role("R&D Intern - Wireless Systems Engineering") == "hardware"
    assert classify_role("Digital Logic + Design Verification Graduate Intern") == "hardware"
    assert classify_role("Digital Health Algorithms Intern") == "swe"
    assert classify_role("IT & Cybersecurity Leadership Development Internship Program") == "swe"
    assert classify_role("Malware Research Intern") == "swe"


def test_manual_link_categories_normalizes_and_tolerates_missing_file(tmp_path):
    f = tmp_path / "manual_categories.yaml"
    f.write_text(yaml.safe_dump({
        "https://example.com/jobs/9?utm_source=x": "__drop__",
        "https://example.com/jobs/10": "swe",
    }))
    assert manual_link_categories(f) == {
        "https://example.com/jobs/9": "__drop__",
        "https://example.com/jobs/10": "swe",
    }
    assert manual_link_categories(tmp_path / "nope.yaml") == {}


def test_known_link_locations_maps_normalized_link_to_location(tmp_path):
    (tmp_path / "swe.yaml").write_text(yaml.safe_dump([
        {"link": "https://example.com/jobs/1?utm_source=x", "location": "New York, NY"},
        {"link": "https://example.com/jobs/2", "location": None},
    ]))
    known = known_link_locations(tmp_path)
    assert known == {"https://example.com/jobs/1": "New York, NY"}
