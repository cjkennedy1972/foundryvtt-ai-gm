"""Run an automated playtest against the Umbraeth test world.

    python -m playtest --scenario playtest/scenarios/example.json

The engine must already be running with the campaign started, since the GM side is the engine itself.
The run stops before acting if the precheck finds a problem.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Dict, List

from config import settings
from playtest.accounts import AccountsError, load_accounts
from playtest.bot import PlayerBot
from playtest.guard import WorldUser, precheck
from playtest.runner import run_scenario
from playtest.scenario import ScenarioError, load_scenario

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")


async def _main(args: argparse.Namespace) -> int:
    try:
        accounts = load_accounts(args.accounts)
        scenario_name, steps = load_scenario(args.scenario)
    except (AccountsError, ScenarioError, OSError, ValueError) as e:
        print(f"playtest: {e}", file=sys.stderr)
        return 2

    labels = {p.label for p in accounts.players}
    unknown = sorted({s.player for s in steps} - labels)
    if unknown:
        print(f"playtest: scenario uses players not in the accounts file: {', '.join(unknown)}", file=sys.stderr)
        return 2

    bots: Dict[str, PlayerBot] = {}
    try:
        for account in accounts.players:
            bot = PlayerBot(account)
            bots[account.label] = bot
            print(f"logging in as {account.username} ({account.label})")
            await bot.connect()

        first = next(iter(bots.values()))
        roster = await first.roster()
        users: List[WorldUser] = [
            WorldUser(u.get("name", ""), bool(u.get("isGM")), bool(u.get("active")))
            for u in roster.get("users", [])
        ]
        problems = precheck(
            roster.get("world", ""), accounts.world, users,
            {p.username for p in accounts.players}, settings.foundry_username,
        )
        if problems:
            print("playtest: not starting:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 3

        print(f"precheck ok in '{accounts.world}'; running '{scenario_name}' ({len(steps)} steps)")
        report = await run_scenario(scenario_name, steps, bots, settings.foundry_username)
    finally:
        for bot in bots.values():
            try:
                await bot.close()
            except Exception as e:  # a failed close must not hide the run's result
                print(f"playtest: closing a bot failed: {e}", file=sys.stderr)

    os.makedirs(REPORTS, exist_ok=True)
    slug = os.path.splitext(os.path.basename(args.scenario))[0]
    path = os.path.join(REPORTS, f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"report written to {path}")
    print(f"steps: {len(report['steps'])}, failed: {report['failed']}")
    return 0 if report["failed"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated playtest against the Umbraeth test world.")
    parser.add_argument("--accounts", default=os.path.join(HERE, "accounts.local.json"),
                        help="player accounts file (default: playtest/accounts.local.json)")
    parser.add_argument("--scenario", required=True, help="scenario JSON file")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
