import pytest
import yaml

from categorize import (
    DROP,
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


def test_classify_role_routes_electronics_disciplines_to_hardware():
    # swe's bare `engineer` used to claim all of these.
    assert classify_role("Mechanical Engineering Intern (Summer 2027)") == "hardware"
    assert classify_role("Electrical Engineering Intern") == "hardware"
    assert classify_role("Optical Engineer Co-Op") == "hardware"
    assert classify_role(
        "Raytheon Electrical Engineering Intern (Summer 2027)(Onsite)") == "hardware"
    # In-scope specialties still win over the discipline rule.
    assert classify_role("Mechanical Engineer, Data Analytics Intern") == "data_science"


def test_classify_role_drops_out_of_scope_engineering_disciplines():
    # These routed to hardware, which turned hardware.yaml into the catch-all
    # for every non-software discipline rather than chip/FPGA/embedded work.
    assert classify_role("Intern, Industrial Engineering") == "__drop__"
    assert classify_role("WED - Intern Civil Engineer (Summer 2027)") == "__drop__"
    assert classify_role("Chemical Engineering Intern") == "__drop__"
    assert classify_role("Biomedical Engineering Intern") == "__drop__"
    assert classify_role("Materials Engineering Intern") == "__drop__"
    assert classify_role("Materials Science Intern") == "__drop__"
    assert classify_role("Manufacturing Engineer - Intern") == "__drop__"
    assert classify_role(
        "Boeing Summer 2027 Internship Program (Paid) - Quality Engineering Intern"
    ) == "__drop__"
    assert classify_role(
        "Boeing Summer 2027 Internship Program (Paid) - Facilities Engineering"
    ) == "__drop__"
    assert classify_role("Custom Packaging Design Engineer Co-Op") == "__drop__"
    assert classify_role(
        "Repair Structures Intern - Aftermarket Sustainment Engineering") == "__drop__"
    # In the out-of-scope family at the bottom of the table too, but nothing
    # naming "engineer" reaches it — swe claims the row first.
    assert classify_role("Thermal Engineer Intern - Summer 2027") == "__drop__"
    assert classify_role("Thermal Application Engineer Intern") == "__drop__"
    assert classify_role("Sustainability Engineer Intern") == "__drop__"
    # Hardware is checked before the drop list, so a title naming both keeps
    # its discipline instead of dropping on the out-of-scope word.
    assert classify_role(
        "Mechanical Engineering & System Packaging Intern") == "hardware"


def test_discipline_rules_do_not_require_adjacency_to_engineer():
    # A `<discipline>\s+engineer` rule missed both of these — one puts a word
    # between the two, the other inverts them — and swe claimed them.
    assert classify_role("Mechanical Design Engineering Intern") == "hardware"
    assert classify_role("Student Engineering Intern - Civil") == "__drop__"
    assert classify_role("Intern-Engineering (MEMS Design)") == "hardware"
    assert classify_role(
        "2027 Returning Intern - Microwave/Semiconductor Engineer") == "hardware"
    assert classify_role(
        "2027 Operations Manufacturing Engineering Intern") == "__drop__"


def test_software_titles_are_never_claimed_by_a_discipline_rule():
    # Dropping the adjacency requirement means only this guard stops a
    # discipline word anywhere in a software title from stealing the row.
    assert classify_role(
        "Privacy and Civil Liberties Software Engineer Intern") == "swe"
    assert classify_role(
        "Software Engineer Intern (TikTok-Generalized Arch-Code Intelligence "
        "& Quality Validation) - 2027 Summer") == "swe"
    assert classify_role(
        "2026 Intern Conversion - Aerospace Software Apps Engineer I") == "swe"
    assert classify_role("Manufacturing Systems Developer Intern") == "swe"
    # A bare CS title matches nothing else, so without `computer science` in
    # the swe rule this dropped on the `materials` in its own subtitle.
    assert classify_role(
        "Computer Science Intern - Advanced Structures and Materials") == "swe"
    assert classify_role(
        "Research Intern - School of Computer Science - LTI") == "swe"
    # \b guards: "Infrastructure" contains "structur", and an unguarded
    # substring check dropped these AI/platform roles.
    assert classify_role(
        "AI Infrastructure Engineer Intern (Compute Efficiency)") == "ai_ml"
    assert classify_role("Infrastructure Engineer Intern") == "swe"


def test_watch_list_fallback_families_resolve_deterministically():
    # fetch_companies files an unclassifiable board posting under the
    # company's watch-list category, so these landed in swe (Uline, HNTB) and
    # data_science (Caterpillar) rather than being dropped.
    assert classify_role("Warehouse Management Internship - Summer 2027") == "__drop__"
    assert classify_role("Sales Analyst Internship - Summer 2027") == "__drop__"
    assert classify_role(
        "Returning Intern Inspector - Summer 2027 (Southeast Division)") == "__drop__"
    assert classify_role(
        "Returning Intern/Co-op Planner/Project Controls - NED Summer 2027") == "__drop__"
    assert classify_role(
        "2027 Summer Corporate Intern - Environmental, Health and Safety") == "__drop__"
    assert classify_role(
        "PW1100G Propulsion Systems Analysis Intern (Summer 2027) (Onsite)") == "__drop__"


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


def test_reinforcement_learning_titles_reach_ai_ml():
    # "Reinforcement Learning" carries neither \bml\b nor \bai\b, so these
    # titles used to match no rule at all and park in unclassified.json —
    # five such rows already sit in data/ai_ml.yaml, categorized upstream.
    assert classify_role("Reinforcement Learning Planning Research Intern") == "ai_ml"
    assert classify_role(
        "PhD Research Scientist Intern - Reinforcement Learning for Diffusion Modelling"
    ) == "ai_ml"
    # An in-scope keyword still can't override an earlier rule.
    assert classify_role("Quantitative Researcher Intern - Reinforcement Learning") == "quant"


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


def test_drop_rules_do_not_match_program_or_team_names():
    # `recruit` used to fire on TikTok's program NAME ("Global Frontier Tech
    # Recruitment Program"), silently dropping 8 legitimate AI/ML roles at
    # classify time, and on XPENG's "2027 Campus Recruiting Robotics Center".
    # `content` used to fire on team names like "Data-Content Intelligence".
    assert classify_role(
        "Applied Scientist Intern - Business Integrity - Global Frontier Tech "
        "Recruitment Program - 2027 Start") == "ai_ml"
    assert classify_role(
        "Applied Scientist Intern - Trust and Safety - Multimodal Foundation "
        "Model - Global Frontier Tech Recruitment Program - 2027 Start") == "ai_ml"
    assert classify_role(
        "Research Scientist Intern (TikTok-Data-Content Intelligence) - 2027 Start"
    ) == "ai_ml"
    assert classify_role("2027 Campus Recruiting Robotics Cente...") is None


def test_drop_rules_still_catch_the_real_hr_and_content_functions():
    # Narrowing must not free the roles the patterns exist for.
    assert classify_role("Recruiting Intern") == "__drop__"
    assert classify_role("Recruiting Coordinator Intern") == "__drop__"
    assert classify_role("Talent Acquisition Intern") == "__drop__"
    assert classify_role("Content Strategy Intern") == "__drop__"
    assert classify_role("Content Marketing Intern") == "__drop__"
    assert classify_role("Content Moderation Intern") == "__drop__"
    assert classify_role("Content Creator Intern") == "__drop__"


def test_applied_scientist_does_not_claim_physical_science_titles():
    # ai_ml is evaluated before the DROP rule that owns \bmaterials\b and
    # chemist, so a bare `applied scientist` alternative would silently
    # outrank them — and the retro-classification sweep cannot see that
    # failure, since rule and file would agree.
    assert classify_role("Applied Scientist - Materials Science") != "ai_ml"
    assert classify_role("Applied Scientist Intern, Battery Materials") != "ai_ml"
    assert classify_role("Applied Scientist Intern - Chemistry") != "ai_ml"
    # A bare `metallurg` alternative has the same defect `chemistr` had: the
    # group's trailing \b cannot fire before the "y" in "metallurgy".
    assert classify_role("Applied Scientist Intern - Metallurgy") != "ai_ml"
    # Computational biology IS in scope for ai_ml — the guard must not
    # over-correct.
    assert classify_role("Applied Scientist Intern - Computational Biology") == "ai_ml"
    # The titles Task 1 exists to free must still reach ai_ml.
    assert classify_role(
        "Applied Scientist Intern - Business Integrity - Global Frontier Tech "
        "Recruitment Program - 2027 Start") == "ai_ml"


def test_ic_design_and_rf_titles_route_to_hardware():
    # 2026-09-01: Neuralink's restored "Analog and Mixed-Signal IC Design
    # Engineer Intern" title fell through to swe on the bare `engineer` match.
    assert classify_role("Analog and Mixed-Signal IC Design Engineer Intern") == "hardware"
    assert classify_role("Digital IC Design Engineer Intern") == "hardware"
    assert classify_role("RF Engineer Intern") == "hardware"


@pytest.mark.parametrize("role, expected", [
    ("Technology Intern", "swe"),
    ("Digital Technology Intern - Summer 2027", "swe"),
    ("IT Infrastructure Intern - Summer 2027", "swe"),
    ("Information Security Co-op - Identity & Access Management", "swe"),
    ("Artificial Intelligence Intern", "ai_ml"),
    ("Large Language Models Intern - Research", "ai_ml"),
    ("Ph.D. Research Autonomous Vehicles Intern", "ai_ml"),
    ("Perception Intern - Summer 2027", "ai_ml"),
    ("Trading Analyst Intern", "quant"),
    ("Trading Intern - Summer 2027 - DV Commodities", "quant"),
    ("Commercial Intern - Supply, Trading, & Shipping", DROP),
    ("Data Intern - Key Technology & Services - Data Track", "data_science"),
    ("Statistics Intern", "data_science"),
    ("Predictive Modeler Intern - Summer 2027", "data_science"),
    ("Technology Internship Program", DROP),
])
def test_recurring_tracker_families_resolve_deterministically(role, expected):
    assert classify_role(role) == expected


def test_computing_is_swe():
    # LLNL's "Computing Graduate Student Intern" matched no rule and was
    # filed under its watch-list category on 2026-09-02.
    from categorize import classify_role
    assert classify_role("Computing Graduate Student Intern - Summer 2027") == "swe"
