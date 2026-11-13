"""Repository abstraction interfaces for data persistence.

Defines the generic base repository contract to decouple application services
from direct SQLite database interactions.
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")
ID = TypeVar("ID")


class BaseRepository(ABC, Generic[T, ID]):
    """Generic abstract base repository defining standard persistence operations."""

    @abstractmethod
    def get_by_id(self, entity_id: ID) -> T | None:
        """Retrieve an entity by its unique identifier.

        Args:
            entity_id: The unique identifier of the entity.

        Returns:
            The entity if found, None otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def list_all(self) -> list[T]:
        """Retrieve all persisted entities for this repository.

        Returns:
            A list containing all retrieved entities.
        """
        raise NotImplementedError

    @abstractmethod
    def save(self, entity: T) -> T:
        """Persist or update an entity.

        Args:
            entity: The entity instance to save.

        Returns:
            The persisted entity instance.
        """
        raise NotImplementedError

    @abstractmethod
    def delete(self, entity_id: ID) -> bool:
        """Delete an entity by its unique identifier.

        Args:
            entity_id: The unique identifier of the entity to delete.

        Returns:
            True if the entity was deleted, False if not found.
        """
        raise NotImplementedError
