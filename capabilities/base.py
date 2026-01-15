"""
Capability基底クラス

すべてのCapabilityはこのクラスを継承する。
Capabilityは動詞ベースで、実装詳細を隠蔽する。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from dataclasses import dataclass
from enum import Enum


class CapabilityCategory(Enum):
    """Capabilityのカテゴリ"""
    VISION = "見る"
    COMMUNICATION = "送る"
    SCHEDULE = "覚える"
    MEMORY = "記録する"


@dataclass
class CapabilityResult:
    """Capability実行結果"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None

    @classmethod
    def ok(cls, message: str, data: Optional[Dict[str, Any]] = None) -> 'CapabilityResult':
        """成功結果を作成"""
        return cls(success=True, message=message, data=data)

    @classmethod
    def fail(cls, message: str) -> 'CapabilityResult':
        """失敗結果を作成（人間らしいメッセージで）"""
        return cls(success=False, message=message)


class Capability(ABC):
    """Capability基底クラス"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Capability名（動詞ベース）"""
        pass

    @property
    @abstractmethod
    def category(self) -> CapabilityCategory:
        """カテゴリ"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """説明（AIが理解するため）"""
        pass

    @property
    def requires_confirmation(self) -> bool:
        """確認が必要か（送信・公開・取消不可な操作）"""
        return False

    @abstractmethod
    def execute(self, **kwargs) -> CapabilityResult:
        """実行"""
        pass

    def get_tool_definition(self) -> Dict[str, Any]:
        """Gemini API用のツール定義を取得"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self._get_parameters()
        }

    @abstractmethod
    def _get_parameters(self) -> Dict[str, Any]:
        """パラメータ定義を取得"""
        pass
