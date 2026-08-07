from transformation_scanner.enrichment import normalize_structured_facts


def test_normalizes_control_acquisition_and_financing_language() -> None:
    facts = normalize_structured_facts(
        "acquisition",
        {
            "iscmp_cmpnm": "에이디에스테크",
            "iscmp_nt": "대한민국",
            "iscmp_mbsn": "광통신 액티브 정렬 장비",
            "inhdtl_inhprc": "280,000,000,000",
            "inhdtl_tast": "180,000,000,000",
            "inhdtl_tast_vs": "155.6",
            "inhdtl_ecpt": "120,000,000,000",
            "inhdtl_ecpt_vs": "233.3",
            "atinh_eqrt": "87.5",
            "inh_pp": "경영권 확보 및 신규사업 진출",
            "dl_pym": "전환사채 및 인수금융으로 조달",
        },
    )

    assert facts["target_name"] == "에이디에스테크"
    assert facts["acquisition_amount"] == 280_000_000_000
    assert facts["asset_ratio_pct"] == 155.6
    assert facts["post_stake_pct"] == 87.5
    assert facts["control_acquisition"] is True
    assert facts["new_business_language"] is True
    assert facts["debt_like_payment"] is True


def test_normalizes_cb_and_bw_dilution_fields() -> None:
    cb = normalize_structured_facts(
        "cb",
        {
            "bd_tm": "18",
            "bd_fta": "50,000,000,000",
            "bdis_mthn": "사모",
            "cv_prc": "2,895",
            "cvisstk_cnt": "17,271,157",
            "cvisstk_tisstk_vs": "24.35",
            "fdpp_bsninh": "50,000,000,000",
        },
    )
    bw = normalize_structured_facts(
        "bw",
        {
            "bd_tm": "19",
            "bd_fta": "30,000,000,000",
            "bdis_mthn": "사모",
            "ex_prc": "2,895",
            "nstk_isstk_cnt": "10,362,694",
            "nstk_isstk_tisstk_vs": "14.61",
            "fdpp_ocsa": "30,000,000,000",
        },
    )

    assert cb["instrument"] == "CB"
    assert cb["conversion_or_exercise_price"] == 2_895
    assert cb["dilution_shares"] == 17_271_157
    assert cb["funds_business_acquisition"] == 50_000_000_000
    assert bw["instrument"] == "BW"
    assert bw["conversion_or_exercise_price"] == 2_895
    assert bw["dilution_shares"] == 10_362_694
    assert bw["funds_other_company_securities"] == 30_000_000_000


def test_calculates_paid_in_capital_increase_ratio() -> None:
    facts = normalize_structured_facts(
        "equity_issuance",
        {
            "nstk_ostk_cnt": "20,000,000",
            "nstk_estk_cnt": "0",
            "bfic_tisstk_ostk": "80,000,000",
            "bfic_tisstk_estk": "0",
            "ic_mthn": "제3자배정증자",
            "fdpp_op": "10,000,000,000",
        },
    )

    assert facts["new_shares"] == 20_000_000
    assert facts["existing_shares_before"] == 80_000_000
    assert facts["dilution_ratio_pct"] == 25.0
    assert facts["issue_method"] == "제3자배정증자"
