from app.services import php


def test_timestamp_validation_is_always_on():
    """Regression: validate_timestamps was derived as `1 if revalidate > 0 else 0`,
    so the default revalidate_freq of 0 -- which in PHP means "check on every
    request" -- silently turned validation off instead. An edited file was then
    never picked up while any LSAPI worker for the pool stayed alive, which on a
    site with steady traffic is forever."""
    cfg = php.recommend_php_config()
    assert cfg["opcache_validate_timestamps"] == 1


def test_opcache_stays_enabled():
    assert php.recommend_php_config()["opcache_enable"] == 1


def test_revalidate_freq_is_not_negative():
    assert php.recommend_php_config()["opcache_revalidate_freq"] >= 0


def test_rendered_ini_turns_validation_on():
    ini = php.render_php_ini()
    assert "opcache.validate_timestamps = 1" in ini.replace("  ", " ").replace("  ", " ")


def test_rendered_ini_still_enables_opcache():
    ini = php.render_php_ini()
    assert "opcache.enable" in ini
