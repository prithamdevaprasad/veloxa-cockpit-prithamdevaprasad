"""C12 candidate rules: explicit classifications, never a score. SYNTHETIC profiles only."""

from veloxa.resolution.rules import APPROVABLE, CLASSIFICATIONS, Profile, candidates, shape

POP = [
    Profile("e1", "Syn Alpha", frozenset({"syn-alpha@syn.example.test"}), frozenset({("hr.emp_id", "syn-001")})),
    Profile("e2", "Syn Beta Gamma", frozenset({"syn-beta@syn.example.test"}), frozenset({("hr.emp_id", "syn-002")})),
    Profile("e3", "Sam Alder", frozenset(), frozenset()),
]


def test_shapes():
    assert [shape(v) for v in ("a@b.example.test", "SBG", "S. Alpha", "Alpha, S", "dev-a", "syn-001")] == [
        "email", "initials", "initial_surname", "initial_surname", "opaque", "opaque"]


def test_exact_identifiers_match_and_are_the_only_evidence_recorded():
    (c,) = candidates("SYN-ALPHA@syn.example.test", POP)
    assert (c.entity_id, c.classification) == ("e1", "exact_identifier")
    (c,) = candidates("syn-002", POP, id_namespace="hr.emp_id")
    assert (c.entity_id, c.classification) == ("e2", "exact_identifier")
    assert candidates("syn-002", POP, id_namespace="jira.assignee") == []      # another namespace: no match
    assert not any(hasattr(c, "score") or hasattr(c, "confidence") for c in candidates("SBG", POP))


def test_initials_must_be_unique_within_the_context_population():
    (c,) = candidates("SBG", POP)
    assert (c.entity_id, c.classification) == ("e2", "unique_initials_in_context")
    both = candidates("SA", POP)                        # Syn Alpha and Sam Alder
    assert {c.entity_id for c in both} == {"e1", "e3"} and {c.classification for c in both} == {"ambiguous"}
    (only,) = candidates("SA", POP[:1])                 # a different context population resolves differently
    assert only.classification == "unique_initials_in_context"


def test_surname_initial_forms():
    for v in ("S. Gamma", "S Gamma", "Gamma, S"):
        (c,) = candidates(v, POP)
        assert (c.entity_id, c.classification) == ("e2", "surname_initial")


def test_opaque_identifiers_and_near_misses_stay_unresolved():
    for v in ("dev-a", "Syn Alpah", "syn-alpha@other.example.test", "XYZ"):
        assert candidates(v, POP) == []                 # similarity never suggests, never merges


def test_disagreeing_rules_are_conflicting_and_never_approvable():
    pop = POP + [Profile("e4", "Ann Bee", frozenset({"ab@syn.example.test"}))]
    assert {c.classification for c in candidates("ab@syn.example.test", pop)} == {"exact_identifier"}
    pop2 = [Profile("e5", "Ann Bee", frozenset({"AB"})), Profile("e6", "Al Bo")]   # an e-mail-less identifier
    assert candidates("AB", pop2)[0].classification == "ambiguous"
    clash = [Profile("e7", "Xi Yu", frozenset(), frozenset({("hr.emp_id", "XY")})), Profile("e8", "Xa Ya")]
    out = candidates("XY", clash, id_namespace="hr.emp_id")
    assert {c.classification for c in out} == {"conflicting"} and {c.entity_id for c in out} == {"e7", "e8"}
    assert APPROVABLE == set(CLASSIFICATIONS) - {"ambiguous", "conflicting"}
