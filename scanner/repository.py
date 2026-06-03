import logging

from django.db.models import Count, Max
from django.utils import timezone

from .domain import ProbeResult
from .models import Node

logger = logging.getLogger(__name__)


class NodeRepository:
    """
    Single place for all Node ORM operations.
    Callers never touch Node.objects directly — changes to persistence
    logic are confined here.
    """

    def get_all(self) -> list[Node]:
        return list(Node.objects.all())

    def delete_by_pks(self, pks: list[int]) -> None:
        if not pks:
            return
        deleted, _ = Node.objects.filter(pk__in=pks).delete()
        logger.info("Deleted %d non-working nodes", deleted)

    def persist(
        self,
        new_results: dict[str, ProbeResult],
        retested_nodes: list[Node],
        retested_keys: set[str],
    ) -> None:
        """Upsert newly discovered working nodes and bulk-update retested survivors."""
        deduped: dict[tuple, Node] = {}
        for key, result in new_results.items():
            if key in retested_keys:
                continue
            c = result.config
            dk = (c.protocol, c.host, c.port, c.user_id)
            if dk not in deduped:
                deduped[dk] = Node(
                    protocol=c.protocol,
                    raw_link=c.raw_link,
                    host=c.host,
                    port=c.port,
                    user_id=c.user_id,
                    remark=c.remark,
                    last_speed_kbps=result.speed_kbps,
                    last_checked=timezone.now(),
                    is_working=True,
                )

        candidates = list(deduped.values())
        if candidates:
            tuples = [(n.protocol, n.host, n.port, n.user_id) for n in candidates]
            existing_qs = Node.objects.filter(
                protocol__in=[t[0] for t in tuples],
                host__in=[t[1] for t in tuples],
                port__in=[t[2] for t in tuples],
                user_id__in=[t[3] for t in tuples],
            )
            existing_map = {(n.protocol, n.host, n.port, n.user_id): n for n in existing_qs}
            to_create: list[Node] = []
            to_update: list[Node] = []

            for node in candidates:
                dk = (node.protocol, node.host, node.port, node.user_id)
                if dk in existing_map:
                    db_node = existing_map[dk]
                    db_node.raw_link = node.raw_link
                    db_node.remark = node.remark
                    db_node.last_speed_kbps = node.last_speed_kbps
                    db_node.last_checked = node.last_checked
                    db_node.is_working = True
                    to_update.append(db_node)
                else:
                    to_create.append(node)

            if to_update:
                Node.objects.bulk_update(
                    to_update,
                    ['raw_link', 'remark', 'last_speed_kbps', 'last_checked', 'is_working'],
                )
                logger.info("Bulk updated %d nodes", len(to_update))
            if to_create:
                Node.objects.bulk_create(to_create)
                logger.info("Bulk created %d new nodes", len(to_create))

        if retested_nodes:
            Node.objects.bulk_update(
                retested_nodes,
                ['raw_link', 'last_speed_kbps', 'last_checked', 'is_working'],
            )
            logger.info("Re-test updated %d existing nodes", len(retested_nodes))

        if not candidates and not retested_nodes:
            logger.warning("No working configs found in this scan")

    def remove_duplicates(self) -> None:
        """Remove duplicate Node rows, keeping the most recently inserted."""
        duplicates = (
            Node.objects
            .values('protocol', 'host', 'port', 'user_id')
            .annotate(latest_id=Max('id'), count_id=Count('id'))
            .filter(count_id__gt=1)
        )
        ids_to_keep = [d['latest_id'] for d in duplicates]
        if not ids_to_keep:
            return
        deleted, _ = Node.objects.exclude(id__in=ids_to_keep).filter(
            protocol__in=[d['protocol'] for d in duplicates],
            host__in=[d['host'] for d in duplicates],
            port__in=[d['port'] for d in duplicates],
            user_id__in=[d['user_id'] for d in duplicates],
        ).delete()
        if deleted:
            logger.info("Removed %d duplicate nodes", deleted)
