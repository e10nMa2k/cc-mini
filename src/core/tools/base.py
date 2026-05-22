from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ToolResult:
    content: str # 返回内容, 可能会被截断
    is_error: bool = False


class Tool(ABC): # ABC = Abstract Base Class， 抽象基类
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def input_schema(self) -> dict: ...

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult: ...

    def is_read_only(self) -> bool:
        return False

    def to_api_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
