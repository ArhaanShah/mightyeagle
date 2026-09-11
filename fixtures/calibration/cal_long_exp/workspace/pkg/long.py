from typing import Optional, List

def fetch_data() -> Optional[str]:
    return "item"

def run_many() -> List[int]:
    res = []
    # 8 identical call site errors
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    return res
