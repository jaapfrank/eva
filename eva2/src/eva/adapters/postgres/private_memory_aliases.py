"""Current private memory aliases, resolved from live owner authority."""
from eva.adapters.postgres.private_owner import PostgresPrivateOwnerResolver


class PostgresPrivateMemoryAliases(PostgresPrivateOwnerResolver):
    def __call__(self, query) -> tuple[str, ...]:
        return self.aliases(query)
