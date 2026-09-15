from distressed_equity.sec_instruments import _families_conflict


def test_generic_credit_can_refine_to_one_specific_facility_family():
    assert not _families_conflict({"credit"}, {"revolver"})
    assert not _families_conflict({"credit"}, {"term_loan"})


def test_generic_credit_cannot_bridge_revolver_and_term_loan():
    assert _families_conflict({"credit", "revolver"}, {"term_loan"})
    assert _families_conflict({"revolver"}, {"credit", "term_loan"})


def test_generic_credit_cannot_bridge_into_notes():
    assert _families_conflict({"credit"}, {"notes"})
    assert _families_conflict({"notes"}, {"credit"})


def test_multi_specific_family_cluster_is_invalid_even_against_generic_credit():
    assert _families_conflict({"revolver", "term_loan"}, {"credit"})
