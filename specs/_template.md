# Module Name

## Purpose

一句話說明此模組存在的原因與它在 pipeline 中的定位。

## Behavior

條列式行為規則。這是 Claude 實作時的約束來源。

- **必須**：...
- **不得**：...
- **若 A 則 B**：...

## Interface

關鍵函數的輸入/輸出型別與語意（不需要列出全部，只列有歧義或不直覺的部分）。

```python
def function_name(
    param: type,  # 說明非顯而易見的語意
) -> return_type:
    """一句話說明做什麼。"""
```

## Design Decisions

已解決的設計爭議。每條說明：選了什麼、為何選、捨棄的替代方案是什麼。

**防止 Claude 或開發者重新踩坑。**

### DD-1: 決策標題

- **選擇**：...
- **原因**：...
- **捨棄**：...（及其問題）

## Acceptance Criteria

可測試的驗收條件，未來可轉為 pytest。

- [ ] 情境 A → 預期輸出 B
- [ ] 邊界情境 C → 預期行為 D
