import requests
import json
import time
import random
import os
import sys
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich import box

# ===== CONFIG =====
BASE_URL = "https://moltarena.crosstoken.io/api"
ACCOUNTS_FILE = "accounts.json"

BATTLE_INTERVAL = 300
ACCOUNT_DELAY = (2, 5)
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
POLL_INTERVAL = 15
MAX_WAIT_BATTLE = 600
ROUNDS = 5
CHALLENGE_MODE = False
DEBUG = True

# ===== CONSOLE =====
console = Console()

def log(msg):
    console.print(f"[dim][{datetime.now().strftime('%H:%M:%S')}][/dim] {msg}")

def log_ok(msg):
    console.print(f"[dim][{datetime.now().strftime('%H:%M:%S')}][/dim] ✅ [green]{msg}[/green]")

def log_err(msg):
    console.print(f"[dim][{datetime.now().strftime('%H:%M:%S')}][/dim] ❌ [red]{msg}[/red]")

def log_info(msg):
    console.print(f"[dim][{datetime.now().strftime('%H:%M:%S')}][/dim] 🔵 [cyan]{msg}[/cyan]")

def log_warn(msg):
    console.print(f"[dim][{datetime.now().strftime('%H:%M:%S')}][/dim] ⚠️  [yellow]{msg}[/yellow]")

def debug(label, r):
    if not DEBUG:
        return
    try:
        data = r.json()
        console.print(f"  [dim][DEBUG] {r.status_code} {label} → {str(data)[:300]}[/dim]")
    except Exception:
        console.print(f"  [dim][DEBUG] {r.status_code} {label} → {r.text[:200]}[/dim]")

def safe_json(r):
    try:
        return r.json()
    except Exception:
        return {}

# ===== BANNER =====
def print_banner(accounts):
    content = (
        f"[bold cyan]Akun[/bold cyan]    : [white]{len(accounts)}[/white]\n"
        f"[bold cyan]Interval[/bold cyan]: [white]{BATTLE_INTERVAL}s[/white]  "
        f"[bold cyan]Ronde[/bold cyan]: [white]{ROUNDS}[/white]\n"
        f"[bold cyan]Mode[/bold cyan]    : [white]{'Challenge' if CHALLENGE_MODE else 'Auto Match'}[/white]  "
        f"[bold cyan]Debug[/bold cyan]: [white]{'ON' if DEBUG else 'OFF'}[/white]"
    )
    console.print(Panel(
        content,
        title="[bold yellow]⚔️  MOLTARENA AUTO BATTLE BOT[/bold yellow]",
        border_style="yellow",
        box=box.DOUBLE_EDGE,
        padding=(1, 4),
    ))

# ===== ACCOUNTS =====
def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        log_err(f"File {ACCOUNTS_FILE} tidak ditemukan!")
        log("    Buat dari template: cp accounts.example.json accounts.json")
        sys.exit(1)
    with open(ACCOUNTS_FILE) as f:
        data = json.load(f)
    for acc in data:
        if "token" in acc and "apiKey" not in acc:
            acc["apiKey"] = acc.pop("token")
        acc.setdefault("battleId", None)
        acc.setdefault("agentId", None)
        acc.setdefault("agentName", None)
    return data

def save_accounts(accs):
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accs, f, indent=2)

# ===== REQUEST HELPER =====
def retry_request(func, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                log_err(f"Request gagal setelah {max_retries} retry: {type(e).__name__}: {e}")
    return None

def get_headers(acc):
    return {
        "Authorization": f"Bearer {acc['apiKey']}",
        "Content-Type": "application/json"
    }

# ===== API CALLS =====
def get_my_agents(acc):
    endpoints = ["/agents/me", "/agents", "/deploy/list"]
    for ep in endpoints:
        r = retry_request(lambda ep=ep: requests.get(
            f"{BASE_URL}{ep}", headers=get_headers(acc), timeout=REQUEST_TIMEOUT))
        if r is None:
            continue
        debug(f"GET {ep}", r)
        if r.status_code == 200:
            data = safe_json(r)
            agents = data.get("data") or data.get("agents") or data.get("results") or []
            if isinstance(agents, list):
                return agents
    return []

def start_battle(acc):
    payload = {"rounds": ROUNDS}
    if acc.get("agentId"):
        payload["agentId"] = acc["agentId"]
    if CHALLENGE_MODE:
        payload["mode"] = "challenge"

    endpoints = ["/battles", "/deploy/battle", "/battle/start"]
    for ep in endpoints:
        r = retry_request(lambda ep=ep: requests.post(
            f"{BASE_URL}{ep}", headers=get_headers(acc),
            json=payload, timeout=REQUEST_TIMEOUT))
        if r is None:
            continue
        debug(f"POST {ep}", r)
        if r.status_code in (200, 201):
            data = safe_json(r)
            battle = data.get("data") or data
            bid = battle.get("id") or battle.get("battleId")
            if bid:
                return bid
    return None

def get_battle_status(battle_id, acc):
    r = retry_request(lambda: requests.get(
        f"{BASE_URL}/battles/{battle_id}",
        headers=get_headers(acc), timeout=REQUEST_TIMEOUT))
    if r is None:
        return {}
    debug(f"GET /battles/{str(battle_id)[:8]}...", r)
    return safe_json(r).get("data") or safe_json(r)

def check_notifications(acc):
    endpoints = ["/notifications/poll", "/notifications"]
    for ep in endpoints:
        r = retry_request(lambda ep=ep: requests.get(
            f"{BASE_URL}{ep}", headers=get_headers(acc), timeout=REQUEST_TIMEOUT))
        if r is None:
            continue
        if r.status_code == 200:
            return safe_json(r).get("data") or []
    return []

# ===== DISPLAY =====
def display_agent_info(agent):
    if not agent:
        return
    wins = agent.get("wins", 0)
    losses = agent.get("losses", 0)
    total = wins + losses
    wr = round(wins / total * 100) if total > 0 else 0

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column(style="bold cyan", min_width=12)
    table.add_column(style="white")
    table.add_row("🤖 Agen",   agent.get("name", "?"))
    table.add_row("📊 Rating", str(agent.get("rating", 0)))
    table.add_row("🏅 Rank",   f"#{agent.get('rank', '?')}")
    table.add_row("🎯 Record", f"{wins}W - {losses}L  ({wr}%)")
    console.print(table)

def display_battle_result(battle_data, agent_name):
    if not battle_data:
        return
    winner = battle_data.get("winner") or {}
    winner_name = winner.get("name", "?") if isinstance(winner, dict) else str(winner)
    opponent = (battle_data.get("opponent") or {}).get("name", "?")
    rating_change = battle_data.get("ratingChange", 0)
    old_rating = battle_data.get("oldRating", 0)
    new_rating = battle_data.get("newRating", 0)
    won = winner_name == agent_name
    sign = "+" if rating_change >= 0 else ""

    result = Text()
    result.append("  MENANG! 🏆\n" if won else "  KALAH 💀\n",
                  style="bold green" if won else "bold red")
    result.append(f"  vs {opponent}", style="white")
    if old_rating and new_rating:
        result.append(
            f"\n  Rating: {old_rating} → {new_rating}  ({sign}{rating_change})",
            style="bold green" if rating_change >= 0 else "bold red"
        )

    console.print(Panel(
        result,
        title="[bold]Hasil Battle[/bold]",
        border_style="green" if won else "red",
        box=box.ROUNDED,
        padding=(0, 2),
    ))

def display_cycle_summary(cycle, ok, fail):
    table = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0, 3))
    table.add_column("Siklus", style="bold yellow")
    table.add_column("Berhasil", style="bold green")
    table.add_column("Gagal", style="bold red")
    table.add_column("Waktu", style="dim")
    table.add_row(f"#{cycle}", str(ok), str(fail), datetime.now().strftime("%H:%M:%S"))
    console.print(table)

# ===== NOTIFICATIONS =====
def handle_notifications(accounts):
    for acc in accounts:
        events = check_notifications(acc)
        for event in events:
            etype = event.get("type", "")
            msg = event.get("message", "")
            icons = {
                "battle_complete": "🔔",
                "top100": "🎉",
                "rank_change": "📈",
                "challenge": "⚔️",
            }
            log(f"{icons.get(etype, '📌')} [bold][{acc['name']}][/bold] {etype}: {msg}")

# ===== BATTLE LOOP =====
def run_battle_for_account(acc):
    name = acc.get("name", "Unknown")
    console.print(f"\n[bold white][[>>] Akun: {name}][/bold white]")

    agents = get_my_agents(acc)
    if not agents:
        log_err(f"Tidak ada agen ditemukan untuk {name}")
        log_warn("Buat agen dulu di: https://moltarena.crosstoken.io/agents/new")
        return False

    selected = next((a for a in agents if a.get("name") == acc.get("agentName")), agents[0])
    acc["agentName"] = selected.get("name")
    acc["agentId"] = selected.get("id")
    display_agent_info(selected)

    log_info(f"Memulai battle ({ROUNDS} ronde, mode: {'Challenge' if CHALLENGE_MODE else 'Auto Match'})...")
    battle_id = start_battle(acc)

    if not battle_id:
        log_err("Gagal memulai battle — cek DEBUG output di atas")
        return False

    log_ok(f"Battle dimulai! ID: {str(battle_id)[:12]}...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Menunggu hasil battle...", total=None)

        waited = 0
        while waited < MAX_WAIT_BATTLE:
            time.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL

            battle_data = get_battle_status(battle_id, acc)
            status = str(battle_data.get("status", "")).lower()
            progress.update(task, description=f"[cyan]Menunggu... status: {status or 'running'} ({waited}s)")

            if status in ("finished", "completed", "done", "ended"):
                log_ok(f"Battle selesai! ({waited}s)")
                display_battle_result(battle_data, acc.get("agentName"))
                acc["battleId"] = None
                return True

            if status in ("cancelled", "error", "failed"):
                log_err(f"Battle dibatalkan/error: {status}")
                acc["battleId"] = None
                return False

    log_warn(f"Timeout menunggu battle selesai ({MAX_WAIT_BATTLE}s)")
    acc["battleId"] = None
    return False

# ===== MAIN =====
def main():
    accounts = load_accounts()
    print_banner(accounts)

    console.rule("[cyan]Validasi Akun[/cyan]")
    valid = []
    for acc in accounts:
        agents = get_my_agents(acc)
        if agents is not None:
            log_ok(f"{acc.get('name')} — {len(agents)} agen ditemukan")
            valid.append(acc)
        else:
            log_err(f"{acc.get('name')} — API key invalid!")

    if not valid:
        log_err("Tidak ada akun valid. Cek accounts.json kamu.")
        sys.exit(1)

    log_ok(f"{len(valid)} akun siap. Mulai loop battle...")

    cycle = 0
    while True:
        try:
            cycle += 1
            console.rule(f"[bold yellow]SIKLUS #{cycle} — {datetime.now().strftime('%H:%M:%S')}[/bold yellow]")

            handle_notifications(valid)

            results = {"ok": 0, "fail": 0}
            for i, acc in enumerate(valid):
                ok = run_battle_for_account(acc)
                save_accounts(valid)
                results["ok" if ok else "fail"] += 1
                if i < len(valid) - 1:
                    d = random.uniform(*ACCOUNT_DELAY)
                    log_info(f"Jeda {d:.1f}s sebelum akun berikutnya...")
                    time.sleep(d)

            display_cycle_summary(cycle, results["ok"], results["fail"])
            log_info(f"Tunggu {BATTLE_INTERVAL}s sebelum siklus berikutnya...")
            time.sleep(BATTLE_INTERVAL)

        except KeyboardInterrupt:
            log_warn("Bot dihentikan.")
            save_accounts(valid)
            sys.exit(0)
        except Exception as e:
            log_err(f"ERROR: {type(e).__name__}: {e}")
            log_warn("Retry dalam 30s...")
            time.sleep(30)

if __name__ == "__main__":
    main()
