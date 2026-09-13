"""Bounded recent-window reads, matching twitter-tracker's smart page budget."""
from .provider import user_data


class FollowingWindowError(Exception):
    pass


def page_budget(saved_count):
    # 20 accounts per requested page, plus one page of room for new follows.
    return min(5, max(2, (max(0, saved_count) + 39) // 20))


async def fetch_window(provider, client, user_id, saved_count):
    page = await provider.request(client.get_user_following, user_id, count=20)
    users, cursors = {}, set()
    budget = page_budget(saved_count)
    for index in range(budget):
        for user in page:
            item = user_data(user)
            users[item['id']] = item
        cursor = getattr(page, 'next_cursor', None)
        if not cursor or cursor == '0':
            return list(users.values()), index + 1
        if cursor in cursors:
            raise FollowingWindowError('Repeated following cursor; saved window unchanged.')
        cursors.add(cursor)
        if index + 1 == budget:
            return list(users.values()), index + 1
        # All pages use the same authenticated client. Failures discard the whole read.
        page = await provider.request(page.next)
