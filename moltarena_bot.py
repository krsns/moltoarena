# -*- coding: utf-8 -*-
import requests, json, time, random, os, sys
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich import box

BASE_URL = 'https://moltarena.crosstoken.io/api'
ACCOUNTS_FILE = 'accounts.json'
BATTLE_INTERVAL = 620
ACCOUNT_DELAY = (3, 6)
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
POLL_INTERVAL = 15
MAX_WAIT_BATTLE = 900
ROUNDS = 5
STRATEGY = 'similar_rating'
DEBUG = True
VOTE_DELAY = (1, 3)
MAX_VOTE_PER_CYCLE = 50

console = Console()

def log(msg): console.print(f'[dim][{datetime.now().strftime("%H:%M:%S")}][/dim] {msg}')
def log_ok(msg): console.print(f'[dim][{datetime.now().strftime("%H:%M:%S")}][/dim] [green]OK  {msg}[/green]')
def log_err(msg): console.print(f'[dim][{datetime.now().strftime("%H:%M:%S")}][/dim] [red]ERR {msg}[/red]')
def log_info(msg): console.print(f'[dim][{datetime.now().strftime("%H:%M:%S")}][/dim] [cyan]INF {msg}[/cyan]')
def log_warn(msg): console.print(f'[dim][{datetime.now().strftime("%H:%M:%S")}][/dim] [yellow]WRN {msg}[/yellow]')

def debug(label, r):
    if not DEBUG: return
    try: console.print(f'  [dim][DEBUG] {r.status_code} {label} -> {str(r.json())[:300]}[/dim]')
    except: console.print(f'  [dim][DEBUG] {r.status_code} {label} -> {r.text[:200]}[/dim]')

def safe_json(r):
    try: return r.json()
    except: return {}

# ===== LOAD/SAVE ACCOUNTS =====
def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        log_err('accounts.json belum ada. Isi manual dulu.')
        sys.exit(1)
    with open(ACCOUNTS_FILE) as f:
        data = json.load(f)
    for acc in data:
        if 'token' in acc and 'apiKey' not in acc:
            acc['apiKey'] = acc.pop('token')
        acc.setdefault('battleId', None)
        acc.setdefault('agentIndex', 0)
        acc.setdefault('myAgentIds', [])
    return data

def save_accounts(accs):
    with open(ACCOUNTS_FILE, 'w') as f:
        json.dump(accs, f, indent=2)

def retry_request(func, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                log_err(f'Request gagal: {type(e).__name__}: {e}')
    return None

def get_headers(acc):
    return {
        'Authorization': f'Bearer {acc["apiKey"]}',
        'Content-Type': 'application/json'
    }

# ===== AGENTS – HANYA DARI myAgentIds =====
def get_agent_detail(agent_id, acc):
    r = retry_request(lambda: requests.get(
        f'{BASE_URL}/agents/{agent_id}',
        headers=get_headers(acc),
        timeout=REQUEST_TIMEOUT
    ))
    if r is None:
        return None
    debug(f'GET /agents/{agent_id[:8]}...', r)
    if r.status_code == 200:
        data = safe_json(r)
        return data.get('agent') or data.get('data') or data
    return None

def get_my_agents(acc):
    my_ids = acc.get('myAgentIds', [])
    agents = []
    for aid in my_ids:
        ag = get_agent_detail(aid, acc)
        if ag:
            ag['id'] = ag.get('id') or aid
            agents.append(ag)
        else:
            log_warn(f'Agent ID {aid[:8]}... tidak ditemukan, skip.')
    if not agents:
        log_err('Tidak ada agent dari myAgentIds yang berhasil dimuat. Cek accounts.json.')
    return agents

def get_account_stats(acc):
    for ep in ['/account/stats', '/account', '/me', '/profile']:
        r = retry_request(lambda ep=ep: requests.get(
            f'{BASE_URL}{ep}',
            headers=get_headers(acc),
            timeout=REQUEST_TIMEOUT
        ))
        if r and r.status_code == 200:
            data = safe_json(r)
            inner = data.get('data') or data.get('account') or data.get('user') or data
            if DEBUG:
                console.print(f'  [dim][STATS DEBUG] {ep} -> keys: {list(inner.keys())}[/dim]')
            return inner
    return {}

# ===== BATTLE =====
def start_battle(acc, agent_id):
    payload = {'agent1Id': agent_id, 'rounds': ROUNDS, 'strategy': STRATEGY}
    r = retry_request(lambda: requests.post(
        f'{BASE_URL}/deploy/battle',
        headers=get_headers(acc),
        json=payload,
        timeout=REQUEST_TIMEOUT
    ))
    if r is None:
        return None
    debug('POST /deploy/battle', r)
    if r.status_code in (200, 201):
        data = safe_json(r)
        battle = data.get('battle') or data.get('data') or data
        return battle.get('id') or battle.get('battleId')
    if r.status_code == 429:
        data = safe_json(r)
        wait = int(data.get('retryAfter', 610)) + 10
        next_at = data.get('nextAvailableAt', '-')
        log_warn(f'Rate limited! Next: {next_at} | Tunggu {wait}s...')
        time.sleep(wait)
        r2 = retry_request(lambda: requests.post(
            f'{BASE_URL}/deploy/battle',
            headers=get_headers(acc),
            json=payload,
            timeout=REQUEST_TIMEOUT
        ))
        if r2 and r2.status_code in (200, 201):
            d2 = safe_json(r2)
            b2 = d2.get('battle') or d2.get('data') or d2
            return b2.get('id') or b2.get('battleId')
    return None

def get_battle_status(battle_id, acc):
    r = retry_request(lambda: requests.get(
        f'{BASE_URL}/battles/{battle_id}',
        headers=get_headers(acc),
        timeout=REQUEST_TIMEOUT
    ))
    if r is None:
        return {}
    debug(f'GET /battles/{str(battle_id)[:8]}...', r)
    return safe_json(r).get('data') or safe_json(r)

# ===== VOTE =====
def get_active_battles(acc):
    battles = []
    for ep in ['/battles?status=voting', '/battles?status=active',
               '/battles/active', '/battles/voting', '/battles?limit=50']:
        r = retry_request(lambda ep=ep: requests.get(
            f'{BASE_URL}{ep}',
            headers=get_headers(acc),
            timeout=REQUEST_TIMEOUT
        ))
        if r is None:
            continue
        debug(f'GET {ep}', r)
        if r.status_code == 200:
            data = safe_json(r)
            items = data.get('battles') or data.get('data') or data.get('results') or []
            if isinstance(items, list) and items:
                battles = items
                break
    return battles

def cast_vote(acc, battle_id, agent_id):
    endpoints_payloads = [
        (f'/battles/{battle_id}/vote',       {'agentId': agent_id}),
        (f'/battles/{battle_id}/vote',       {'votedAgentId': agent_id}),
        (f'/vote',                           {'battleId': battle_id, 'agentId': agent_id}),
        (f'/battles/{battle_id}/cast-vote',  {'agentId': agent_id}),
    ]
    for ep, payload in endpoints_payloads:
        r = retry_request(lambda ep=ep, p=payload: requests.post(
            f'{BASE_URL}{ep}',
            headers=get_headers(acc),
            json=p,
            timeout=REQUEST_TIMEOUT
        ))
        if r is None:
            continue
        debug(f'POST {ep}', r)
        if r.status_code in (200, 201):
            return True, safe_json(r)
        if r.status_code == 400:
            data = safe_json(r)
            if 'already' in str(data).lower():
                return 'already_voted', data
    return False, {}

def run_auto_vote(acc):
    console.rule('[bold magenta]AUTO VOTE[/bold magenta]')
    battles = get_active_battles(acc)
    if not battles:
        log_warn('Tidak ada battle aktif untuk di-vote.')
        return 0, 0
    log_info(f'Ditemukan {len(battles)} battle | Mulai vote...')
    voted = 0
    skipped = 0
    failed = 0
    limit = min(len(battles), MAX_VOTE_PER_CYCLE)
    table = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0, 2))
    table.add_column('Battle',   style='dim',   width=10)
    table.add_column('Vote For', style='cyan',  min_width=16)
    table.add_column('Status',   style='bold',  width=14)
    for battle in battles[:limit]:
        battle_id = battle.get('id', '')
        participants = battle.get('participants') or {}
        a1 = participants.get('agent1') or battle.get('agent1') or {}
        a2 = participants.get('agent2') or battle.get('agent2') or {}
        candidates = [a for a in (a1, a2) if a.get('id')]
        if not candidates:
            table.add_row(str(battle_id)[:8], '-', '[dim]no agents[/dim]')
            skipped += 1
            continue
        pick = random.choice(candidates)
        pick_id = pick.get('id')
        pick_name = pick.get('name', str(pick_id)[:8])
        result, resp = cast_vote(acc, battle_id, pick_id)
        if result is True:
            bp_gain = (resp.get('data') or resp).get('pointsEarned', '')
            bp_str = f' +{bp_gain}BP' if bp_gain else ''
            table.add_row(str(battle_id)[:8], pick_name, f'[green]VOTED{bp_str}[/green]')
            voted += 1
        elif result == 'already_voted':
            table.add_row(str(battle_id)[:8], pick_name, '[dim]already[/dim]')
            skipped += 1
        else:
            table.add_row(str(battle_id)[:8], pick_name, '[red]FAIL[/red]')
            failed += 1
        time.sleep(random.uniform(*VOTE_DELAY))
    console.print(table)
    console.print(Panel(
        f'[green]Voted  : {voted}[/green]\n[dim]Skipped: {skipped}[/dim]\n[red]Failed : {failed}[/red]',
        title='Vote Summary',
        border_style='magenta',
        box=box.ROUNDED,
        padding=(0, 3)
    ))
    return voted, failed

# ===== DISPLAY =====
def display_agents_table(agents, current_idx):
    table = Table(title='My Agents', box=box.ROUNDED, border_style='cyan', padding=(0, 2))
    table.add_column('#',      style='dim',        width=3)
    table.add_column('Nama',   style='bold white', min_width=14)
    table.add_column('Rating', style='yellow',     justify='right')
    table.add_column('W',      style='green',      justify='right')
    table.add_column('L',      style='red',        justify='right')
    table.add_column('WR%',    style='magenta',    justify='right')
    table.add_column('Status', style='bold',       justify='center')
    for idx, a in enumerate(agents):
        wins = a.get('wins', 0)
        losses = a.get('losses', 0)
        total = wins + losses
        wr = f'{round(wins / total * 100)}%' if total > 0 else '-'
        rating = str(round(float(a.get('rating', 0)), 1))
        status = '[bold green]GILIRAN[/bold green]' if idx == current_idx else '[dim]standby[/dim]'
        table.add_row(str(idx + 1), a.get('name', '?'), rating,
                      str(wins), str(losses), wr, status)
    console.print(table)

def display_account_stats(acc_name, stats, agents):
    bp = (stats.get('battlePoints') or stats.get('bp') or
          stats.get('points') or stats.get('tokens') or '?')
    total_w = sum(a.get('wins', 0) for a in agents)
    total_l = sum(a.get('losses', 0) for a in agents)
    total = total_w + total_l
    wr = f'{round(total_w / total * 100)}%' if total > 0 else '-'
    best = max(agents, key=lambda a: float(a.get('rating', 0)), default={})
    txt = (
        f'[bold cyan]Akun[/bold cyan]         : [white]{acc_name}[/white]\n'
        f'[bold cyan]Battle Points[/bold cyan]: [bold yellow]{bp}[/bold yellow]\n'
        f'[bold cyan]Total Battle[/bold cyan] : [white]{total}[/white]  '
        f'([green]{total_w}W[/green] / [red]{total_l}L[/red] | {wr})\n'
        f'[bold cyan]Best Agent[/bold cyan]   : [white]{best.get("name","?")}[/white] '
        f'| Rating [yellow]{round(float(best.get("rating",0)),1)}[/yellow]'
    )
    console.print(Panel(txt,
                        title='[bold]Account Stats[/bold]',
                        border_style='yellow',
                        box=box.ROUNDED,
                        padding=(1, 3)))

def display_battle_result(battle_data, agent_name):
    if not battle_data:
        return
    winner = battle_data.get('winner') or {}
    winner_name = winner.get('name', '?') if isinstance(winner, dict) else str(winner)
    opponent = (battle_data.get('opponent') or {}).get('name', '?')
    rc = battle_data.get('ratingChange', 0)
    old_r = battle_data.get('oldRating', 0)
    new_r = battle_data.get('newRating', 0)
    battle_id = battle_data.get('id', '')
    rounds = battle_data.get('rounds', [])
    won = winner_name == agent_name
    sign = '+' if rc >= 0 else ''
    wp = []
    wr_c = 0
    lc = 0
    for rd in rounds:
        rw = rd.get('winner') or {}
        rn = rw.get('name', '') if isinstance(rw, dict) else str(rw)
        if rn == agent_name:
            wp.append('[green][W][/green]')
            wr_c += 1
        elif rn == '':
            wp.append('[dim][D][/dim]')
        else:
            wp.append('[red][L][/red]')
            lc += 1
    result = Text()
    result.append('  MENANG!\n' if won else '  KALAH\n',
                  style='bold green' if won else 'bold red')
    result.append(f'  vs {opponent}', style='white')
    if old_r and new_r:
        result.append(f'\n  Rating : {old_r} -> {new_r}  ({sign}{rc})',
                      style='bold green' if rc >= 0 else 'bold red')
    console.print(Panel(result,
                        title=f'Hasil Battle - {agent_name}',
                        border_style='green' if won else 'red',
                        box=box.ROUNDED,
                        padding=(0, 2)))
    if wp:
        color = 'green' if rc >= 0 else 'red'
        share = (
            f'[bold yellow]AGENT ARENA #{str(battle_id)[:8]}[/bold yellow]\n\n'
            f'[cyan]{agent_name}[/cyan] vs [cyan]{opponent}[/cyan]\n\n'
            f'{" ".join(wp)}\n\n'
            f'[white]Result: {wr_c}-{lc} {"Victory!" if won else "Defeat"}[/white]\n'
            f'[{color}]Rating: {old_r} -> {new_r}  ({sign}{rc})[/{color}]\n'
            f'[dim]moltarena.crosstoken.io/battle/{battle_id}[/dim]'
        )
        console.print(Panel(share,
                            title='Share Card',
                            border_style='yellow',
                            box=box.ROUNDED,
                            padding=(1, 3)))

def display_cycle_summary(cycle, results_per_agent, vote_ok, vote_fail):
    table = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0, 2))
    table.add_column('Siklus', style='bold yellow')
    table.add_column('Agent',  style='bold cyan')
    table.add_column('Battle', style='bold')
    table.add_column('Waktu',  style='dim')
    for agent_name, ok in results_per_agent:
        table.add_row(
            f'#{cycle}',
            agent_name,
            '[green]OK[/green]' if ok else '[red]FAIL[/red]',
            datetime.now().strftime('%H:%M:%S')
        )
    table.add_row(
        f'#{cycle}',
        '[magenta]AUTO VOTE[/magenta]',
        f'[green]{vote_ok} voted[/green] | [red]{vote_fail} fail[/red]',
        datetime.now().strftime('%H:%M:%S')
    )
    console.print(table)

def handle_notifications(accounts):
    icons = {
        'battle_complete': '[BATTLE]',
        'top100': '[TOP100]',
        'rank_change': '[RANK]',
        'challenge': '[CHALLENGE]',
    }
    for acc in accounts:
        for event in check_notifications(acc):
            etype = event.get('type', '')
            msg = event.get('message', '')
            log(f'[bold cyan]{icons.get(etype,"[NOTIF]")}[/bold cyan] [{acc["name"]}] {etype}: {msg}')

def check_notifications(acc):
    for ep in ['/notifications/poll', '/notifications']:
        r = retry_request(lambda ep=ep: requests.get(
            f'{BASE_URL}{ep}',
            headers=get_headers(acc),
            timeout=REQUEST_TIMEOUT
        ))
        if r and r.status_code == 200:
            return safe_json(r).get('data') or []
    return []

# ===== MAIN LOOP =====
def print_banner(accounts):
    console.print(Panel(
        f'[bold cyan]Akun[/bold cyan]    : [white]{len(accounts)}[/white]\n'
        f'[bold cyan]Interval[/bold cyan]: [white]{BATTLE_INTERVAL}s[/white]  [bold cyan]Ronde[/bold cyan]: [white]{ROUNDS}[/white]\n'
        f'[bold cyan]Strategy[/bold cyan]: [white]{STRATEGY}[/white]  [bold cyan]MaxVote[/bold cyan]: [white]{MAX_VOTE_PER_CYCLE}[/white]',
        title='MOLTARENA AUTO BATTLE + VOTE BOT',
        border_style='yellow',
        box=box.DOUBLE_EDGE,
        padding=(1, 4)
    ))

def run_battle_for_agent(acc, agent):
    agent_name = agent.get('name', '?')
    agent_id = agent.get('id')
    log_info(f'Battle: [bold]{agent_name}[/bold] | Rating: {round(float(agent.get("rating", 0)), 1)}')
    battle_id = start_battle(acc, agent_id)
    if not battle_id:
        log_err(f'Gagal mulai battle {agent_name}')
        return False
    log_ok(f'Battle dimulai! ID: {str(battle_id)[:12]}...')
    with Progress(
        SpinnerColumn(),
        TextColumn('{task.description}'),
        TimeElapsedColumn(),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task(f'[cyan]Menunggu {agent_name}...', total=None)
        waited = 0
        while waited < MAX_WAIT_BATTLE:
            time.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL
            bd = get_battle_status(battle_id, acc)
            status = str(bd.get('status', '')).lower()
            progress.update(task, description=f'[cyan]{agent_name} | {status or "running"} ({waited}s)')
            if status in ('finished', 'completed', 'done', 'ended', 'voting'):
                log_ok(f'Battle selesai! ({waited}s)')
                display_battle_result(bd, agent_name)
                return True
            if status in ('cancelled', 'error', 'failed'):
                log_err(f'Battle {status}: {agent_name}')
                return False
    log_warn(f'Timeout {agent_name}')
    return False

def main():
    accounts = load_accounts()
    print_banner(accounts)
    valid = []
    console.rule('[cyan]Validasi Akun & Load Agents[/cyan]')
    for acc in accounts:
        agents = get_my_agents(acc)
        if not agents:
            log_err(f'{acc.get("name")} -- tidak ada agent dari myAgentIds.')
            continue
        acc['_agents'] = agents
        stats = get_account_stats(acc)
        acc['_stats'] = stats
        log_ok(f'{acc.get("name")} -- {len(agents)} agent dimuat')
        display_account_stats(acc.get('name'), stats, agents)
        display_agents_table(agents, acc.get('agentIndex', 0))
        valid.append(acc)

    if not valid:
        log_err('Tidak ada akun valid.')
        sys.exit(1)

    log_ok(f'{len(valid)} akun siap.')
    cycle = 0
    while True:
        try:
            cycle += 1
            console.rule(f'[bold yellow]SIKLUS #{cycle} -- {datetime.now().strftime("%H:%M:%S")}[/bold yellow]')
            handle_notifications(valid)
            results_summary = []
            total_voted = 0
            total_vfail = 0

            for acc in valid:
                agents = acc.get('_agents', [])
                if not agents:
                    continue
                idx = acc.get('agentIndex', 0) % len(agents)
                agent = agents[idx]
                console.print(
                    f'\n[bold white][ >> {acc["name"]} | Battle {idx+1}/{len(agents)}: {agent.get("name")} ][/bold white]'
                )
                ok = run_battle_for_agent(acc, agent)
                results_summary.append((agent.get('name', '?'), ok))
                acc['agentIndex'] = (idx + 1) % len(agents)
                save_accounts([{k: v for k, v in a.items() if not k.startswith("_")} for a in valid])

                v_ok, v_fail = run_auto_vote(acc)
                total_voted += v_ok
                total_vfail += v_fail

                if acc != valid[-1]:
                    d = random.uniform(*ACCOUNT_DELAY)
                    log_info(f'Jeda {d:.1f}s...')
                    time.sleep(d)

            display_cycle_summary(cycle, results_summary, total_voted, total_vfail)

            for acc in valid:
                acc['_agents'] = get_my_agents(acc)
                acc['_stats'] = get_account_stats(acc)
                if acc['_agents']:
                    display_account_stats(acc.get('name'), acc['_stats'], acc['_agents'])
                    display_agents_table(acc['_agents'], acc.get('agentIndex', 0))

            log_info(f'Tunggu {BATTLE_INTERVAL}s sebelum siklus berikutnya...')
            time.sleep(BATTLE_INTERVAL)

        except KeyboardInterrupt:
            log_warn('Bot dihentikan.')
            save_accounts([{k: v for k, v in a.items() if not k.startswith("_")} for a in valid])
            sys.exit(0)
        except Exception as e:
            log_err(f'ERROR: {type(e).__name__}: {e}')
            log_warn('Retry dalam 30s...')
            time.sleep(30)

if __name__ == '__main__':
    main()
