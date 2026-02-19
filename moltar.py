import requests, json, time, random, os, sys
from datetime import datetime

# ===== CONFIG =====
BASE_URL       = "https://moltarena.crosstoken.io/api"
ACCOUNTS_FILE  = "accounts.json"

BATTLE_INTERVAL   = 300    # Jeda antar siklus battle (detik)
ACCOUNT_DELAY     = (2, 5) # Jeda acak antar akun (detik)
REQUEST_TIMEOUT   = 30
MAX_RETRIES       = 3
POLL_INTERVAL     = 10     # Cek status battle setiap N detik
MAX_WAIT_BATTLE   = 600    # Maks tunggu hasil battle (detik)
ROUNDS            = 5      # Ronde per battle: 3 / 5 / 7 / 10
CHALLENGE_MODE    = False  # True = challenge top ranker

# ==================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def safe_json(r):
    try:
        return r.json()
    except:
        return {}

# ===== ACCOUNTS =====

def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        log(f"[!] '{ACCOUNTS_FILE}' tidak ditemukan.")
        log(f"    Salin dulu: cp accounts.json.example accounts.json")
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

def req(method, path, acc=None, **kwargs):
    """Wrapper request dengan retry otomatis."""
    headers = {}
    if acc:
        headers["Authorization"] = f"Bearer {acc['apiKey']}"
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.request(method, f"{BASE_URL}{path}", headers=headers, **kwargs)
            return r
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2)
            else:
                log(f"     [!] Request gagal ({MAX_RETRIES}x): {type(e).__name__}")
    return None

# ===== API CALLS =====

def get_my_agents(acc):
    r = req("GET", "/deploy/list", acc)
    if r is None:
        return None
    data = safe_json(r)
    return data.get("data") or data.get("agents") or []

def start_battle(acc):
    payload = {"rounds": ROUNDS}
    if acc.get("agentId"):
        payload["agentId"] = acc["agentId"]
    if CHALLENGE_MODE:
        payload["mode"] = "challenge"

    r = req("POST", "/deploy/battle", acc, json=payload)
    if r is None:
        return None

    data = safe_json(r)
    if not data.get("success"):
        err = data.get("error", {})
        msg = err.get("message") if isinstance(err, dict) else str(err)
        log(f"     [!] Gagal start battle: {msg}")
        return None

    battle = data.get("data", {})
    return battle.get("id") or battle.get("battleId")

def get_battle_status(battle_id, acc):
    r = req("GET", f"/battles/{battle_id}", acc)
    if r is None:
        return {}
    return safe_json(r).get("data", {})

def check_notifications(acc):
    r = req("GET", "/notifications/poll", acc)
    if r is None:
        return []
    return safe_json(r).get("data") or []

# ===== DISPLAY =====

def show_agent(agent):
    if not agent:
        return
    name    = agent.get("name", "?")
    rating  = agent.get("rating", "?")
    rank    = agent.get("rank", "?")
    wins    = agent.get("wins", 0)
    losses  = agent.get("losses", 0)
    total   = wins + losses
    wr      = f"{round(wins/total*100)}%" if total else "0%"
    log(f"  🤖 {name} | Rating: {rating} | Rank: #{rank} | {wins}W-{losses}L ({wr})")

def show_result(battle, agent_name):
    if not battle:
        return
    winner      = battle.get("winner", {})
    winner_name = winner.get("name", "?") if isinstance(winner, dict) else str(winner)
    opponent    = battle.get("opponent", {})
    opp_name    = opponent.get("name", "?") if isinstance(opponent, dict) else str(opponent)
    rounds      = battle.get("rounds", [])
    old_r       = battle.get("oldRating", 0)
    new_r       = battle.get("newRating", 0)
    diff        = battle.get("ratingChange", new_r - old_r)

    won = winner_name == agent_name
    log(f"  {'🏆 MENANG' if won else '💀 KALAH'}! vs {opp_name}")

    if rounds:
        row = " ".join([
            f"R{i+1}{'🟢' if r.get('winner') == agent_name else '🔴'}"
            for i, r in enumerate(rounds)
        ])
        log(f"  {row}")

    if old_r and new_r:
        sign = "+" if diff >= 0 else ""
        log(f"  📊 Rating: {old_r} → {new_r} ({sign}{diff})")

# ===== BATTLE LOGIC =====

def run_battle(acc):
    label = acc.get("name", "?")
    log(f"\n[>>] {label}")

    # Ambil daftar agen
    agents = get_my_agents(acc)
    if agents is None:
        log(f"  [!] Gagal ambil agen (cek API key)")
        return False
    if not agents:
        log(f"  [!] Belum punya agen. Buat dulu di website MoltArena.")
        return False

    # Pilih agen
    selected = next((a for a in agents if a.get("name") == acc.get("agentName")), agents[0])
    acc["agentName"] = selected.get("name")
    acc["agentId"]   = selected.get("id")
    show_agent(selected)

    # Mulai battle
    log(f"  ⚔️  Start battle ({ROUNDS} ronde, {'challenge' if CHALLENGE_MODE else 'auto match'})...")
    battle_id = start_battle(acc)
    if not battle_id:
        return False

    acc["battleId"] = battle_id
    log(f"  [+] Battle ID: {battle_id[:8]}...")

    # Polling hasil
    for elapsed in range(0, MAX_WAIT_BATTLE, POLL_INTERVAL):
        time.sleep(POLL_INTERVAL)
        data   = get_battle_status(battle_id, acc)
        status = data.get("status", "")

        if status in ("finished", "completed", "done"):
            show_result(data, acc["agentName"])
            acc["battleId"] = None
            return True

        if status in ("cancelled", "error", "failed"):
            log(f"  [!] Battle {status}")
            acc["battleId"] = None
            return False

        log(f"  [~] {status or 'running'} ... {elapsed + POLL_INTERVAL}s")

    log(f"  [!] Timeout ({MAX_WAIT_BATTLE}s)")
    acc["battleId"] = None
    return False

# ===== NOTIF HANDLER =====

def process_notifications(accounts):
    for acc in accounts:
        events = check_notifications(acc)
        for ev in events:
            etype = ev.get("type", "")
            msg   = ev.get("message", "")
            label = acc.get("name", "?")
            if etype == "top100":
                log(f"  🎉 [{label}] MASUK TOP 100! {msg}")
            elif etype == "rank_change":
                log(f"  📈 [{label}] Rank berubah! {msg}")
            elif etype == "challenge":
                log(f"  ⚔️  [{label}] Ditantang! {msg}")
            elif etype == "battle_complete":
                log(f"  🔔 [{label}] Battle selesai! {msg}")

# ===== MAIN =====

def main():
    accounts = load_accounts()

    log("=" * 55)
    log("  MOLTARENA AUTO BATTLE BOT")
    log(f"  Akun: {len(accounts)} | Interval: {BATTLE_INTERVAL}s | Ronde: {ROUNDS}")
    log(f"  Mode: {'Challenge' if CHALLENGE_MODE else 'Auto Match'}")
    log("=" * 55)

    # Validasi akun
    log("\n[~] Validasi akun...")
    valid = []
    for acc in accounts:
        agents = get_my_agents(acc)
        if agents is not None:
            log(f"  ✅ {acc['name']} — {len(agents)} agen")
            valid.append(acc)
        else:
            log(f"  ❌ {acc['name']} — API key gagal!")

    if not valid:
        log("\n[X] Tidak ada akun valid. Cek accounts.json.")
        sys.exit(1)

    log(f"\n[+] {len(valid)} akun aktif. Loop dimulai...\n")

    cycle = 0
    while True:
        try:
            cycle += 1
            log(f"\n{'='*55}")
            log(f"  SIKLUS #{cycle}")
            log(f"{'='*55}")

            process_notifications(valid)

            ok, fail = 0, 0
            for i, acc in enumerate(valid):
                if run_battle(acc):
                    ok += 1
                else:
                    fail += 1
                save_accounts(valid)

                if i < len(valid) - 1:
                    d = random.uniform(*ACCOUNT_DELAY)
                    log(f"\n  [~] Jeda {d:.1f}s...\n")
                    time.sleep(d)

            log(f"\n[*] Siklus #{cycle}: {ok} berhasil, {fail} gagal")
            log(f"[~] Tunggu {BATTLE_INTERVAL}s...\n")
            time.sleep(BATTLE_INTERVAL)

        except KeyboardInterrupt:
            log("\n[X] Dihentikan.")
            save_accounts(valid)
            sys.exit(0)

        except Exception as e:
            log(f"\n[-] Error: {type(e).__name__}: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
