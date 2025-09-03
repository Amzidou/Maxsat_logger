from maxsat_runner.core.parser import parse_o, is_optimum

def test_parse_o_ok():
    assert parse_o("o 123") == 123
    assert parse_o("  o   -7  ") == -7
    assert parse_o("\to\t42") == 42

def test_parse_o_ko():
    assert parse_o("o x42") is None
    assert parse_o("p cnf") is None
    assert parse_o("c comment") is None

def test_is_optimum():
    assert is_optimum("s OPTIMUM FOUND")
    assert is_optimum("   s   OPTIMUM FOUND   ")
    assert is_optimum("  s  OPTIMUM    FOUND ")
    assert not is_optimum("s SATISFIABLE")

