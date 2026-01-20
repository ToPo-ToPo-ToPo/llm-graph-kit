import textwrap
from typing import Any

class GraphLogger:
    """
    プログラムの内容に依存せず、ターミナル表示の「スタイル」を提供する汎用ロガー。
    """

    # 色設定
    COLORS = {
        "HEADER": '\033[95m', "BLUE": '\033[94m', "CYAN": '\033[96m',
        "GREEN": '\033[92m', "YELLOW": '\033[93m', "RED": '\033[91m',
        "ENDC": '\033[0m', "BOLD": '\033[1m'
    }

    @classmethod
    def print_phase_header(cls, title: str, emoji: str = "🚀"):
        """メインフェーズの開始を目立つように表示します"""
        c = cls.COLORS
        print(f"\n{c['BLUE']}{c['BOLD']}" + "="*70 + f"{c['ENDC']}")
        print(f"{c['BLUE']}{c['BOLD']} {emoji}  {title} {c['ENDC']}")
        print(f"{c['BLUE']}{c['BOLD']}" + "="*70 + f"{c['ENDC']}")

    @classmethod
    def print_subtask_start(cls, index: int, task_name: str):
        """サブタスクの開始を表示します"""
        c = cls.COLORS
        print(f"\n{c['YELLOW']}┌── 🔸 Subtask {index} ──────────────────────────────────────────────────{c['ENDC']}")
        print(f"{c['YELLOW']}│ Task: {c['ENDC']}{task_name}")
        print(f"{c['YELLOW']}└──────────────────────────────────────────────────────────────────{c['ENDC']}")

    @classmethod
    def log(cls, style: str, content: Any, title: str = ""):
        """
        スタイルを指定してログを出力します。
        
        Args:
            style (str): 表示スタイル ("header", "box", "list", "info", "success", "error", "code")
            content (Any): 表示内容（文字列、リスト、辞書など）
            title (str): タイトルやラベル（任意）
        """
        c = cls.COLORS
        style = style.lower()
        
        # ---------------------------------------------------------
        # 1. 
        # ---------------------------------------------------------
        if style == "response":
            # タイトルが指定されていなければデフォルトを設定
            display_title = title if title else "Generated Response"
            
            # 色設定 (ここでは CYAN を使用。GREEN にしたい場合は c['GREEN'] に変更可)
            color = c['GREEN'] 
            
            print(f"\n{color}{c['BOLD']}🤖 {display_title}{c['ENDC']}")
            print(f"{color}──────────────────────────────────────────────────────────────{c['ENDC']}")
            
            # 本文も色付きで表示
            print(f"{color}{content}{c['ENDC']}")
            
            print(f"{color}──────────────────────────────────────────────────────────────{c['ENDC']}\n")

        # ---------------------------------------------------------
        # 3. list: 計画や手順の箇条書き
        # ---------------------------------------------------------
        elif style == "list":
            if title:
                print(f"\n{c['BOLD']}📋 {title}:{c['ENDC']}")
            
            if isinstance(content, list):
                for i, item in enumerate(content, 1):
                    print(f"{i}. {item}")
            else:
                print(f"- {content}")

        # ---------------------------------------------------------
        # 4. info: 一般的な情報、ツール選択など（1行表示推奨）
        # ---------------------------------------------------------
        elif style == "info":
            # 辞書が渡された場合は Key: Value 形式で見やすく
            if isinstance(content, dict):
                print(f"{c['CYAN']}🛠  {title}{c['ENDC']}")
                for k, v in content.items():
                    print(f"Running {k}: {v}")
            else:
                label = f"{title}: " if title else ""
                print(f"{c['CYAN']}ℹ️  {label}{c['BOLD']}{content}{c['ENDC']}")

        # ---------------------------------------------------------
        # 5. code / preview: 実行結果などの長文プレビュー
        # ---------------------------------------------------------
        elif style == "code" or style == "preview":
            text = str(content)
            # 長すぎる場合は省略表示
            preview = textwrap.shorten(text, width=200, placeholder="...")
            lines = preview.split('\n')
            if len(lines) > 5:
                preview = "\n".join(lines[:5]) + "\n... (more lines) ..."
            
            label = title if title else "Output"
            print(f"{c['GREEN']}📄 {label}:\n{preview}{c['ENDC']}")
            print(f"{c['GREEN']}──────────────────────────────────────{c['ENDC']}")

        # ---------------------------------------------------------
        # 6. success / error: 評価や完了通知
        # ---------------------------------------------------------
        elif style == "success":
            print(f"{c['GREEN']}✅ {title}: {content}{c['ENDC']}")
            
        elif style == "error":
            print(f"{c['RED']}❌ {title}: {content}{c['ENDC']}")

        # ---------------------------------------------------------
        # 7. fallback: 想定外のスタイル
        # ---------------------------------------------------------
        else:
            prefix = f"[{title}] " if title else ""
            print(f"{prefix}{content}")