from typing import List

def fetch_data() -> str:
    return "item"

def run_many() -> List[int]:
    res = []
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    return res
