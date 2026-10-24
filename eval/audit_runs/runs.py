"""The audited reports: label key -> run folder (repo root relative). Shared by the audit tools."""
RUNS = {
    "raft_20260929_224216": "research_output/how_do_raft_and_paxos_differ_in_leader_e_0ba009_20260929_224216",
    "aiact_20261001_232456": "research_output/what_are_the_latest_developments_in_the_bc7ad7_20261001_232456",
    "fi_et_20261023_181948": "research_output/compare_finland_s_and_estonia_s_approach_37dce8_20261023_181948",
    "h13_psilocybin": "research_output/what_is_known_about_how_psilocybin_works_2013be_20261024_074648",
    "h22_glp1": "research_output/what_is_known_about_how_glp_1_receptor_a_538f3e_20261024_084549",
    "h21_lsm": "research_output/how_do_lsm_trees_and_b_trees_differ_in_w_b4e410_20261024_094554",
    "h12_raft": "research_output/how_do_raft_and_paxos_differ_in_leader_e_0ba009_20261024_101041",
    "h18_congestion": "research_output/what_does_the_evidence_say_for_and_again_dde16e_20261024_104713",
    "h23_flu": "research_output/what_were_the_causes_and_consequences_of_01a41e_20261024_114718",
    "h27_remote": "research_output/what_does_the_evidence_say_for_and_again_a159a6_20261024_124722",
}

# Evidence directories the evidence-only runs were fed (for the query facets). None where the run's evidence set is not kept under eval/.
EVIDENCE = {
    "h13_psilocybin": "eval/heldout3_runs/h13", "h22_glp1": "eval/heldout4_runs/rerun2/h22", "h21_lsm": "eval/heldout4_runs/rerun2/h21",
    "h12_raft": "eval/heldout3_runs/h12", "h18_congestion": "eval/heldout3_runs/h18", "h23_flu": "eval/heldout4_runs/rerun2/h23", "h27_remote": "eval/heldout4_runs/rerun2/h27",
}

# Validation set (cheap check on existing reports, 2026-10-24): label key -> run folder. Labels live in labels_validation/.
VAL_RUNS = {
    "val_fi_20261017_163846": "src/research_output/compare_finland_s_and_estonia_s_approach_37dce8_20261017_163846",
    "val_fi_20261017_174505": "src/research_output/compare_finland_s_and_estonia_s_approach_37dce8_20261017_174505",
    "val_raft_20260929_205408": "research_output/how_do_raft_and_paxos_differ_in_leader_e_0ba009_20260929_205408",
    "val_aiact_20261001_185243": "research_output/what_are_the_latest_developments_in_the_bc7ad7_20261001_185243",
    "val_fi_20261021_214936": "src/research_output/compare_finland_s_and_estonia_s_approach_37dce8_20261021_214936",
    "val_fi_20261021_210135": "src/research_output/compare_finland_s_and_estonia_s_approach_37dce8_20261021_210135",
    "val_fi_20261023_202339": "src/research_output/compare_finland_s_and_estonia_s_approach_37dce8_20261023_202339",
    "val_fi_20261017_190810": "src/research_output/compare_finland_s_and_estonia_s_approach_37dce8_20261017_190810",
}
