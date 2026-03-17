"""
ユーザープロファイル生成・管理

ライフログデータからユーザーの人となり・行動パターン・判断基準を抽出し、
AIがユーザーの代わりに意思決定できるようにするためのプロファイルを生成する
"""

import os
import json
import logging
import requests
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from collections import defaultdict

from dotenv import load_dotenv

from .base import Capability, CapabilityCategory, CapabilityResult
from .vision import get_openai_client
from config import Config

logger = logging.getLogger(__name__)

# 環境変数の読み込み
env_path = os.path.expanduser("~/.ai-necklace/.env")
load_dotenv(env_path)

# Firebase設定
FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL", "")


def get_lifelogs_from_firebase(days: int = 30) -> Dict[str, Any]:
    """FirebaseからライフログデータをN日分取得"""
    if not FIREBASE_DATABASE_URL:
        return {}

    try:
        db_url = f"{FIREBASE_DATABASE_URL}/lifelogs.json"
        response = requests.get(db_url, timeout=30)
        if response.status_code == 200:
            all_data = response.json() or {}

            # 直近N日分のみフィルタリング
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            filtered = {
                date: logs for date, logs in all_data.items()
                if date >= cutoff_date
            }
            return filtered
        return {}
    except Exception as e:
        logger.error(f"ライフログ取得エラー: {e}")
        return {}


def save_profile_to_firebase(profile: Dict[str, Any]) -> bool:
    """プロファイルをFirebaseに保存"""
    if not FIREBASE_DATABASE_URL:
        return False

    try:
        db_url = f"{FIREBASE_DATABASE_URL}/user_profile.json"
        response = requests.put(db_url, json=profile, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"プロファイル保存エラー: {e}")
        return False


def get_profile_from_firebase() -> Optional[Dict[str, Any]]:
    """Firebaseからプロファイルを取得"""
    if not FIREBASE_DATABASE_URL:
        return None

    try:
        db_url = f"{FIREBASE_DATABASE_URL}/user_profile.json"
        response = requests.get(db_url, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        logger.error(f"プロファイル取得エラー: {e}")
        return None


def analyze_time_patterns(lifelogs: Dict[str, Any]) -> Dict[str, Any]:
    """時間帯別の活動パターンを分析"""
    time_activities = defaultdict(list)

    for date, logs in lifelogs.items():
        if not isinstance(logs, dict):
            continue
        for time_key, log in logs.items():
            if not isinstance(log, dict):
                continue
            time_str = log.get("time", "")
            analysis = log.get("analysis", "")
            if time_str and analysis:
                try:
                    hour = int(time_str.split(":")[0])
                    time_activities[hour].append(analysis)
                except (ValueError, IndexError):
                    pass

    # 時間帯をカテゴリに分類
    morning = []  # 5-11
    afternoon = []  # 12-17
    evening = []  # 18-23
    night = []  # 0-4

    for hour, activities in time_activities.items():
        if 5 <= hour < 12:
            morning.extend(activities)
        elif 12 <= hour < 18:
            afternoon.extend(activities)
        elif 18 <= hour < 24:
            evening.extend(activities)
        else:
            night.extend(activities)

    # 最初と最後の活動時間から睡眠パターンを推定
    first_hours = []
    last_hours = []
    for date, logs in lifelogs.items():
        if not isinstance(logs, dict):
            continue
        times = []
        for time_key, log in logs.items():
            if isinstance(log, dict) and log.get("time"):
                try:
                    times.append(int(log["time"].split(":")[0]))
                except (ValueError, IndexError):
                    pass
        if times:
            first_hours.append(min(times))
            last_hours.append(max(times))

    avg_wake = sum(first_hours) / len(first_hours) if first_hours else 7
    avg_sleep = sum(last_hours) / len(last_hours) if last_hours else 23

    sleep_type = "朝型" if avg_wake < 7 else ("夜型" if avg_wake > 9 else "標準")

    return {
        "sleepSchedule": {
            "wakeTime": f"{int(avg_wake):02d}:00",
            "sleepTime": f"{int(avg_sleep):02d}:00",
            "type": sleep_type
        },
        "activityByTime": {
            "morning": len(morning),
            "afternoon": len(afternoon),
            "evening": len(evening),
            "night": len(night)
        }
    }


def analyze_location_patterns(lifelogs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """頻出場所を分析"""
    location_visits = defaultdict(int)

    for date, logs in lifelogs.items():
        if not isinstance(logs, dict):
            continue
        for time_key, log in logs.items():
            if not isinstance(log, dict):
                continue
            location = log.get("location", {})
            if location and location.get("latitude"):
                # 緯度経度を丸めてグループ化（約100m単位）
                lat = round(location["latitude"], 3)
                lng = round(location["longitude"], 3)
                location_visits[f"{lat},{lng}"] += 1

    # 上位10箇所を返す
    sorted_locations = sorted(location_visits.items(), key=lambda x: x[1], reverse=True)[:10]
    return [{"coords": loc, "visits": count} for loc, count in sorted_locations]


def analyze_activity_keywords(lifelogs: Dict[str, Any]) -> Dict[str, int]:
    """活動キーワードを分析"""
    keywords = defaultdict(int)

    activity_categories = {
        "PC作業": ["PC", "パソコン", "作業", "仕事", "デスク"],
        "外出": ["外出", "移動", "電車", "歩", "散歩"],
        "食事": ["食事", "ランチ", "カフェ", "コーヒー", "食べ"],
        "買い物": ["買い物", "スーパー", "店"],
        "自宅": ["自宅", "家", "リビング", "部屋"],
        "会議": ["会議", "ミーティング", "打ち合わせ"],
    }

    for date, logs in lifelogs.items():
        if not isinstance(logs, dict):
            continue
        for time_key, log in logs.items():
            if not isinstance(log, dict):
                continue
            analysis = log.get("analysis", "")
            for category, words in activity_categories.items():
                if any(word in analysis for word in words):
                    keywords[category] += 1

    return dict(keywords)


def generate_profile_with_ai(lifelogs: Dict[str, Any],
                              time_patterns: Dict[str, Any],
                              activity_keywords: Dict[str, int]) -> Dict[str, Any]:
    """AIを使ってライフログからプロファイルを生成"""

    # ライフログのサンプルを抽出（最新100件程度）
    samples = []
    for date in sorted(lifelogs.keys(), reverse=True)[:7]:  # 直近7日
        logs = lifelogs.get(date, {})
        if isinstance(logs, dict):
            for time_key, log in sorted(logs.items()):
                if isinstance(log, dict) and log.get("analysis"):
                    samples.append(f"{date} {log.get('time', '')}: {log['analysis']}")
                if len(samples) >= 100:
                    break
        if len(samples) >= 100:
            break

    samples_text = "\n".join(samples)

    # 活動サマリー
    activity_summary = ", ".join([f"{k}: {v}回" for k, v in
                                   sorted(activity_keywords.items(), key=lambda x: x[1], reverse=True)])

    prompt = f"""以下はあるユーザーの直近のライフログ（自動撮影した写真の説明）です。
このデータから、このユーザーの「人となり」「行動パターン」「価値観」「判断基準」を分析してください。

【ライフログサンプル】
{samples_text}

【活動の集計】
{activity_summary}

【睡眠パターン】
起床時間: 約{time_patterns['sleepSchedule']['wakeTime']}
就寝時間: 約{time_patterns['sleepSchedule']['sleepTime']}
タイプ: {time_patterns['sleepSchedule']['type']}

以下のJSON形式で回答してください：
{{
  "personality": {{
    "summary": "この人の一言での特徴（20文字以内）",
    "traits": ["特徴1", "特徴2", "特徴3"]
  }},
  "lifestyle": {{
    "morning": "午前中の傾向（20文字以内）",
    "afternoon": "午後の傾向（20文字以内）",
    "evening": "夜の傾向（20文字以内）",
    "weekend": "週末の傾向（20文字以内）"
  }},
  "preferences": {{
    "likes": ["好むこと1", "好むこと2", "好むこと3"],
    "dislikes": ["避けること1", "避けること2"]
  }},
  "decisionCriteria": {{
    "scheduling": "予定を入れる際の判断基準",
    "places": "場所選びの判断基準",
    "activities": "活動選択の判断基準"
  }},
  "values": {{
    "efficiency": 0.0〜1.0の数値,
    "routine": 0.0〜1.0の数値,
    "solitude": 0.0〜1.0の数値,
    "socialActivity": 0.0〜1.0の数値,
    "spontaneity": 0.0〜1.0の数値
  }},
  "insights": [
    "AIがユーザーの代わりに判断する際に役立つ洞察1",
    "洞察2",
    "洞察3"
  ],
  "aiGuidelines": "AIがこのユーザーの代わりに判断する際の心得（100文字以内）"
}}"""

    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "あなたはユーザー行動分析の専門家です。ライフログデータから人の特性を正確に読み取ります。"},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            max_tokens=1500
        )

        result = json.loads(response.choices[0].message.content)
        return result
    except Exception as e:
        logger.error(f"AI分析エラー: {e}")
        return {}


def generate_user_profile(days: int = 30) -> Dict[str, Any]:
    """ユーザープロファイルを生成"""

    # ライフログを取得
    lifelogs = get_lifelogs_from_firebase(days)

    if not lifelogs:
        return {"error": "ライフログデータがありません"}

    # 各種分析
    time_patterns = analyze_time_patterns(lifelogs)
    location_patterns = analyze_location_patterns(lifelogs)
    activity_keywords = analyze_activity_keywords(lifelogs)

    # AI分析
    ai_profile = generate_profile_with_ai(lifelogs, time_patterns, activity_keywords)

    # 総データポイント数を計算
    total_logs = sum(
        len(logs) if isinstance(logs, dict) else 0
        for logs in lifelogs.values()
    )

    # プロファイルを構築
    profile = {
        "generatedAt": datetime.now().isoformat(),
        "analyzedDays": days,
        "dataPoints": total_logs,
        "sleepSchedule": time_patterns["sleepSchedule"],
        "activityByTime": time_patterns["activityByTime"],
        "frequentLocations": location_patterns,
        "activityKeywords": activity_keywords,
        **ai_profile
    }

    # Firebaseに保存
    save_profile_to_firebase(profile)

    return profile


class ProfileGenerate(Capability):
    """プロファイル生成"""

    @property
    def name(self) -> str:
        return "profile_generate"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.MEMORY

    @property
    def description(self) -> str:
        return """ライフログからユーザープロファイルを生成・更新する。以下の場面で使う：
- 「自分のことを分析して」「プロファイル更新して」「私のことを覚えて」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "分析する日数（デフォルト30日）"
                }
            }
        }

    def execute(self, days: int = 30) -> CapabilityResult:
        profile = generate_user_profile(days)

        if "error" in profile:
            return CapabilityResult.error(profile["error"])

        summary = profile.get("personality", {}).get("summary", "分析完了")
        data_points = profile.get("dataPoints", 0)

        return CapabilityResult.ok(
            f"プロファイルを更新しました。{data_points}件のログから分析: {summary}"
        )


class ProfileGet(Capability):
    """プロファイル取得"""

    @property
    def name(self) -> str:
        return "profile_get"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.MEMORY

    @property
    def description(self) -> str:
        return """ユーザープロファイルを参照する。以下の場面で使う：
- 「私ってどんな人？」「自分の特徴教えて」
- 判断を求められた時にユーザーの傾向を確認する"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        profile = get_profile_from_firebase()

        if not profile:
            return CapabilityResult.error("プロファイルがまだありません。「プロファイル生成して」と言ってください")

        # 主要情報を抽出
        personality = profile.get("personality", {})
        guidelines = profile.get("aiGuidelines", "")

        summary = personality.get("summary", "")
        traits = personality.get("traits", [])

        result_text = f"【{summary}】\n特徴: {', '.join(traits)}\n\n判断の心得: {guidelines}"

        return CapabilityResult.ok(result_text, data=profile)


# 将来的にAI応答に統合する際に使用
# def get_profile_for_system_prompt() -> str:
#     """システムプロンプト用のプロファイル要約を取得"""
#     profile = get_profile_from_firebase()
#     if not profile:
#         return ""
#     # ... 実装は将来追加


# エクスポート
PROFILE_CAPABILITIES = [
    ProfileGenerate(),
    ProfileGet(),
]
