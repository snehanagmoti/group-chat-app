from lab_config import mapped_application_port


def test_lab_application_port_mapping() -> None:
    assert mapped_application_port(2237, 3000) == 3237
    assert mapped_application_port(2237, 4000) == 4237
    assert mapped_application_port(2238, 5000) == 5238
    assert mapped_application_port(2239, 5000) == 5239
    assert mapped_application_port(2240, 5000) == 5240
