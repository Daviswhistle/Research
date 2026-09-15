from distressed_equity.contract_parties import ContractParty, contract_parties_compatible


def party(role, name):
    return ContractParty(role=role, name=name, normalized_name=name)


def test_source_backed_alias_group_can_match_same_role_party():
    target = (party("borrower", "facebook inc"),)
    candidate = (party("borrower", "meta platforms inc"),)
    aliases = ({"facebook inc", "meta platforms inc"},)
    assert contract_parties_compatible(target, candidate, alias_groups=aliases) is True


def test_alias_group_does_not_override_role_mismatch():
    target = (party("borrower", "facebook inc"),)
    candidate = (party("guarantor", "meta platforms inc"),)
    aliases = ({"facebook inc", "meta platforms inc"},)
    assert contract_parties_compatible(target, candidate, alias_groups=aliases) is False


def test_without_authoritative_alias_group_renamed_names_do_not_match():
    target = (party("borrower", "facebook inc"),)
    candidate = (party("borrower", "meta platforms inc"),)
    assert contract_parties_compatible(target, candidate) is False
