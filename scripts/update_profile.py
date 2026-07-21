#!/usr/bin/env python3
"""Refresh simple GitHub stats in the profile SVG files."""

from __future__ import annotations

import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import UTC, datetime


SVG_FILES = ("dark_mode.svg", "light_mode.svg")
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def github_json(url: str, token: str | None) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-readme-updater",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def github_graphql(query: str, variables: Mapping[str, object], token: str) -> dict[str, object]:
    body = json.dumps({"query": query, "variables": dict(variables)}).encode("utf-8")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "profile-readme-updater",
    }
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError("Unexpected GitHub GraphQL response")
    if result.get("errors"):
        raise RuntimeError(f"GitHub GraphQL error: {result['errors']}")
    return result


def paginated_repos(url: str, token: str | None) -> list[dict[str, object]]:
    repos: list[dict[str, object]] = []
    page = 1
    while True:
        separator = "&" if "?" in url else "?"
        batch = github_json(f"{url}{separator}per_page=100&page={page}", token)
        if not isinstance(batch, list) or not batch:
            return repos
        repos.extend(repo for repo in batch if isinstance(repo, dict))
        page += 1


def public_repos(username: str, token: str | None) -> list[dict[str, object]]:
    return paginated_repos(
        f"https://api.github.com/users/{username}/repos?type=all&sort=updated",
        token,
    )


def authenticated_repos(token: str) -> list[dict[str, object]]:
    return paginated_repos(
        "https://api.github.com/user/repos"
        "?visibility=all&affiliation=owner,collaborator,organization_member&sort=updated",
        token,
    )


def all_time_commit_contributions(username: str, created_at: str, token: str) -> int:
    query = """
      query($login: String!, $from: DateTime!, $to: DateTime!) {
        user(login: $login) {
          contributionsCollection(from: $from, to: $to) {
            contributionCalendar { totalContributions }
          }
        }
      }
    """
    created_year = datetime.fromisoformat(created_at.replace("Z", "+00:00")).year
    current_year = datetime.now(UTC).year
    total = 0

    for year in range(created_year, current_year + 1):
        result = github_graphql(
            query,
            {
                "login": username,
                "from": f"{year}-01-01T00:00:00Z",
                "to": f"{year}-12-31T23:59:59Z",
            },
            token,
        )
        user = result.get("data", {}).get("user") if isinstance(result.get("data"), dict) else None
        if not isinstance(user, dict):
            raise RuntimeError(f"Unexpected GitHub contribution response for {username}")
        collection = user.get("contributionsCollection", {})
        if not isinstance(collection, dict):
            raise RuntimeError(f"Unexpected GitHub contribution collection for {username}")
        calendar = collection.get("contributionCalendar", {})
        if not isinstance(calendar, dict):
            raise RuntimeError(f"Unexpected GitHub contribution calendar for {username}")
        total += int(calendar.get("totalContributions", 0))

    return total


def public_contribution_count(username: str) -> int:
    result = github_json(f"https://github-contributions-api.jogruber.de/v4/{username}?y=all", None)
    if not isinstance(result, dict) or not isinstance(result.get("total"), dict):
        raise RuntimeError(f"Unexpected public contribution response for {username}")
    return sum(int(value) for value in result["total"].values())


def env_value(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


def active_since(created_at: object) -> str:
    created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    return str(created.year)


def set_text(root: ET.Element, element_id: str, value: object) -> None:
    if value is None:
        return
    element = root.find(f".//*[@id='{element_id}']")
    if element is None:
        raise RuntimeError(f"Could not find SVG element #{element_id}")
    element.text = f"{value:,}" if isinstance(value, int) else str(value)


def update_svg(filename: str, stats: dict[str, object | None]) -> None:
    tree = ET.parse(filename)
    root = tree.getroot()
    set_text(root, "repo_data", stats["repos"])
    set_text(root, "active_since_data", stats["active_since"])
    set_text(root, "follower_data", stats["followers"])
    set_text(root, "commit_data", stats["commits"])
    tree.write(filename, encoding="utf-8", xml_declaration=False)


def main() -> None:
    username = (
        os.environ.get("PROFILE_USERNAME")
        or os.environ.get("GITHUB_REPOSITORY_OWNER")
        or os.environ.get("GITHUB_ACTOR")
    )
    if not username:
        raise RuntimeError("Set PROFILE_USERNAME or run inside GitHub Actions.")

    token = env_value("PROFILE_STATS_TOKEN") or env_value("GITHUB_TOKEN")
    stats_token = env_value("PROFILE_STATS_TOKEN")
    user = github_json(f"https://api.github.com/users/{username}", token)
    if not isinstance(user, dict):
        raise RuntimeError(f"Unexpected GitHub user response for {username}")

    repo_override = env_value("PROFILE_REPO_COUNT")
    repos = authenticated_repos(stats_token) if stats_token else public_repos(username, token)
    stats = {
        "repos": repo_override or (len(repos) if stats_token else None),
        "active_since": active_since(user.get("created_at")),
        "followers": int(user.get("followers", 0)),
        "commits": (
            all_time_commit_contributions(username, str(user.get("created_at")), stats_token)
            if stats_token
            else public_contribution_count(username)
        ),
    }

    for filename in SVG_FILES:
        update_svg(filename, stats)


if __name__ == "__main__":
    main()
