"""PHP workers per account, and one MariaDB tuner (operator, 2026-10-06).

On .122 an account with five sites and a two-core CPU limit grew from 13 lsphp
processes to 135 under load -- every vhost said LSAPI_CHILDREN=100 -- and the
kernel killed 54 of them at its 8 GB. What must hold now: an account gets
WORKERS_PER_CORE for each core it may use, split over its sites; the server no
more than its memory holds after MariaDB and a reserve; and MariaDB has one
tuner, a quarter of the RAM at most, with connections that follow the PHP
workers.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import mariadb, openlitespeed, php, php_workers
from app.services.php_workers import Account

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def test_the_122_incident_is_bounded():
    """.122: 8 cores, 16 GB, a 2.5 GB pool. tapseniorca has five sites and a
    200% CPU limit; six more accounts have one site each and no limit."""
    budget = php_workers.server_budget(total_mb=15986, pool_mb=2560)
    assert budget == 76
    accounts = {9: Account(2.0, [f"site{i}.tapsenior.com" for i in range(5)])}
    accounts.update({n: Account(8.0, [f"site{n}.example"]) for n in range(1, 7)})
    plan = php_workers.split(accounts, budget, cores=8)
    tapsenior = [plan[f"site{i}.tapsenior.com"] for i in range(5)]
    assert tapsenior == [2] * 5, "ten workers for the account instead of 75 (135 on the day)"
    assert {plan[f"site{n}.example"] for n in range(1, 7)} == {12}
    assert sum(plan.values()) <= budget + 2 * len(plan)


def test_an_account_gets_three_workers_a_core_split_over_its_sites():
    plan = php_workers.split({1: Account(4.0, ["a.test", "b.test"])}, budget=1000, cores=8)
    assert plan == {"a.test": 6, "b.test": 6}
    plan = php_workers.split({1: Account(0.5, ["a.test"])}, budget=1000, cores=8)
    assert plan == {"a.test": 2}, "never below two a site"


def test_the_memory_budget_caps_a_server_full_of_unlimited_accounts():
    accounts = {n: Account(8.0, [f"s{n}.test"]) for n in range(20)}
    plan = php_workers.split(accounts, budget=40, cores=8)
    assert set(plan.values()) == {2}, "40 workers of memory for 20 accounts: two each, not 24"


def test_the_budget_leaves_mariadb_and_a_reserve():
    assert php_workers.reserve_mb(16000) == 2000
    assert php_workers.reserve_mb(2048) == 1024
    assert php_workers.server_budget(total_mb=2048, pool_mb=512) == 3
    assert php_workers.server_budget(total_mb=1024, pool_mb=256) == php_workers.MIN_PER_SITE


def test_the_pool_is_read_the_way_mariadb_reads_its_files(tmp_path, monkeypatch):
    monkeypatch.setattr(php_workers, "MARIADB_CONF_DIR", tmp_path)
    (tmp_path / "50-server.cnf").write_text("[mysqld]\ninnodb_buffer_pool_size = 128M\n")
    (tmp_path / "90-opanel-tuning.cnf").write_text("[mysqld]\ninnodb_buffer_pool_size = 2560M\n")
    assert php_workers.mariadb_pool_mb() == 2560
    (tmp_path / "99-local.cnf").write_text("[mysqld]\ninnodb_buffer_pool_size = 4G\n")
    assert php_workers.mariadb_pool_mb() == 4096, "the last file wins"


def test_a_cpu_limit_counts_only_while_the_addon_enforces_it():
    user = SimpleNamespace(cpu_percent=200, group_cpu_percent=0)
    reseller = SimpleNamespace(group_cpu_percent=150)
    assert php_workers._allowed_cores(user, None, enforcing=False, cores=8) == 8.0
    assert php_workers._allowed_cores(user, None, enforcing=True, cores=8) == 2.0
    assert php_workers._allowed_cores(user, reseller, enforcing=True, cores=8) == 1.5
    unlimited = SimpleNamespace(cpu_percent=0, group_cpu_percent=0)
    assert php_workers._allowed_cores(unlimited, None, enforcing=True, cores=8) == 8.0


def test_the_vhost_carries_the_planned_number(monkeypatch):
    monkeypatch.setattr(php_workers, "current_plan", lambda fresh=False: {"shop.test": 6})
    monkeypatch.setattr(openlitespeed, "_cgroup_limits_enforced", lambda: True)
    content = openlitespeed.render_vhost("shop.test", "/home/shop/shop.test", app_type="wordpress",
                                         php_version="8.4", linux_user="shop")
    assert "maxConns              6\n" in content and "LSAPI_CHILDREN=6\n" in content
    content = openlitespeed.render_vhost("new.test", "/home/shop/new.test", app_type="php",
                                         php_version="8.4", linux_user="shop")
    assert f"LSAPI_CHILDREN={php_workers.DEFAULT_PER_SITE}\n" in content
    for template in ("php.conf.j2", "wordpress.conf.j2"):
        text = (PROJECT_ROOT / "backend" / "app" / "templates" / "openlitespeed" / template).read_text(encoding="utf-8")
        assert "LSAPI_CHILDREN=100" not in text and "LSAPI_CHILDREN={{ php_workers }}" in text


def test_reconcile_rewrites_only_the_sites_that_moved(monkeypatch):
    import app.api.websites as websites
    import app.core.database as database

    sites = {"a.test": SimpleNamespace(domain="a.test"), "b.test": SimpleNamespace(domain="b.test")}

    class Query:
        def __init__(self, model):
            self.wanted = None

        def filter(self, condition):
            self.wanted = condition.right.value
            return self

        def first(self):
            return sites.get(self.wanted)

    db = SimpleNamespace(query=lambda model: Query(model), __enter__=None)

    class Session:
        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(database, "SessionLocal", Session)
    monkeypatch.setattr(php_workers, "build_plan", lambda session: {"a.test": 6, "b.test": 4, "c.test": 2})
    monkeypatch.setattr(php_workers, "configured", lambda domain: {"a.test": 100, "b.test": 4, "c.test": None}[domain])
    written, reloads = [], []
    monkeypatch.setattr(websites, "_rewrite_website_vhost", lambda site, **kw: written.append((site.domain, kw)))
    monkeypatch.setattr(openlitespeed, "reload_service", lambda: reloads.append(1))
    assert php_workers.reconcile() == ["a.test"]
    assert written == [("a.test", {"defer_reload": True})] and reloads == [1], \
        "one site re-rendered, one restart; a site without lsphp (suspended, static) left alone"


def test_the_minute_tick_reconciles():
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "backup_scheduler.py").read_text(encoding="utf-8")
    assert "php_workers.reconcile_quietly()" in source


def test_memory_limit_is_one_gigabyte_on_every_size():
    assert {tier[1] for tier in php._PHP_TIERS} == {"1024M"}


def test_mariadb_has_one_tuner():
    assert not hasattr(mariadb, "_TIERS") and not hasattr(mariadb, "render_mariadb_conf")
    assert mariadb.TUNING_CONF_FILE.name == "90-opanel-tuning.cnf"
    calc = HELPER.split("\ncalculate_mariadb_tuning() {", 1)[1].split("\n}\n", 1)[0]
    assert "buffer_default=$((total_mb * 25 / 100))" in calc
    assert "local wanted=$((data_mb * 5 / 4))" in calc
    assert "max_connections=$((php_workers + 20))" in calc
    assert "php_workers=$(( (total_mb - buffer_mb - reserve_mb) / 150 ))" in calc
    assert "tmp_mb=64" in calc and "tmp_mb=32" in calc
    write = HELPER.split("\nwrite_mariadb_tuning() {", 1)[1].split("\n}\n", 1)[0]
    assert 'rm -f "$(dirname "$MARIADB_TUNING_CONF")/99-opanel.cnf"' in write, "the old Tune button's file goes"
    retune = HELPER.split("\nretune_mariadb() {", 1)[1].split("\n}\n", 1)[0]
    assert retune.index("if write_mariadb_tuning; then") < retune.index("systemctl restart mariadb"), \
        "a restart only when a setting changed"
    assert "mariadb-tune-preview)" in HELPER


def test_the_panel_numbers_match_the_helpers():
    """The PHP budget and the MariaDB connections come from the same arithmetic
    in two languages; the constants must agree."""
    calc = HELPER.split("\ncalculate_mariadb_tuning() {", 1)[1].split("\n}\n", 1)[0]
    assert f"/ {php_workers.WORKER_MB} ))" in calc
    assert "reserve_mb=$((total_mb / 8))" in calc and "(( reserve_mb >= 1024 )) || reserve_mb=1024" in calc
    assert php_workers.reserve_mb(8 * 1024 * 8) == 8 * 1024


def test_the_preview_is_parsed(monkeypatch):
    output = "innodb_buffer_pool_size=3968M\nmax_connections=96\ntmp_table_size=64M\nram_mb=15986\ncores=8\nphp_workers=76\ninnodb_data_mb=7615\n"
    monkeypatch.setattr(mariadb.shell, "privileged", lambda *a, **k: SimpleNamespace(returncode=0, stdout=output, stderr=""))
    cfg = mariadb.recommend_mariadb_config()
    assert (cfg["innodb_buffer_pool_size"], cfg["max_connections"], cfg["tmp_table_size"],
            cfg["max_heap_table_size"], cfg["ram_mb"], cfg["php_workers"]) == ("3968M", 96, "64M", "64M", 15986, 76)


@pytest.mark.parametrize("value, mb", [("2560M", 2560), ("4G", 4096), ("524288K", 512), ("134217728", 128)])
def test_pool_units(value, mb):
    number = value.rstrip("KkMmGg")
    unit = value[len(number):]
    assert php_workers._megabytes(number, unit) == mb
