import base64
import logging
import re
from typing import Optional

import requests

from .engine import run_full_scan  # noqa: F401 — re-exported for tasks + admin
from .models import Node, Subscription

logger = logging.getLogger(__name__)

_GITHUB_URL_RE = re.compile(
    r'https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)'
)


def publish_confs_to_github(subscription_ids: Optional[list[int]] = None) -> bool:
    """
    Encode all working nodes as base64 and PUT them to each subscription's GitHub file.

    Subscription.token is stored encrypted at rest (django-encrypted-model-fields).
    Returns True if all pushes succeeded, False if any failed.
    """
    qs = Subscription.objects.filter(active=True)
    if subscription_ids:
        qs = qs.filter(id__in=subscription_ids)
    subscriptions = list(qs)

    if not subscriptions:
        logger.warning("publish_confs_to_github: no active subscriptions found")
        return False

    content = '\n'.join(
        Node.objects.filter(is_working=True).values_list('raw_link', flat=True)
    )
    success = True

    for sub in subscriptions:
        m = _GITHUB_URL_RE.match(sub.url)
        if not m:
            logger.error("Invalid GitHub URL for subscription '%s': %s", sub.name, sub.url)
            success = False
            continue

        owner, repo, branch, path = m.groups()
        api_url = f'https://api.github.com/repos/{owner}/{repo}/contents/{path}'
        headers = {'Authorization': f'token {sub.token}'}

        try:
            sha_resp = requests.get(api_url, headers=headers, timeout=10)
            sha: Optional[str] = sha_resp.json().get('sha') if sha_resp.status_code == 200 else None

            payload: dict = {
                'message': 'Update subscription file',
                'content': base64.b64encode(content.encode()).decode(),
                'branch': branch,
            }
            if sha:
                payload['sha'] = sha

            resp = requests.put(api_url, headers=headers, json=payload, timeout=10)
            if resp.status_code in (200, 201):
                logger.info("Subscription '%s' pushed to GitHub", sub.name)
            else:
                logger.error(
                    "Failed to push subscription '%s': HTTP %d — %s",
                    sub.name, resp.status_code, resp.text[:200],
                )
                success = False
        except requests.RequestException as exc:
            logger.error("Network error pushing subscription '%s': %s", sub.name, exc)
            success = False

    return success
