from abc import ABC, abstractmethod
from container import Container


class Debloater(ABC):
    @abstractmethod
    def debloat(self, container: Container) -> str:
        raise NotImplementedError()
