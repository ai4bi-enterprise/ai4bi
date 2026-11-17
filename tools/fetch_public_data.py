"""Tải bộ dữ liệu công khai Microsoft AdventureWorks DW (MIT) vào data/raw/.

Nguồn: https://github.com/microsoft/sql-server-samples/tree/master/samples/databases/adventure-works/data-warehouse-install-script
"""
import os
import sys
import urllib.request

BASE = ("https://raw.githubusercontent.com/microsoft/sql-server-samples/master/samples/databases/"
        "adventure-works/data-warehouse-install-script/")
FILES = ["DimAccount", "DimCurrency", "DimEmployee", "DimProduct", "DimProductCategory", "DimProductSubcategory",
         "DimReseller", "FactCurrencyRate", "FactFinance", "FactInternetSales", "FactProductInventory",
         "FactResellerSales", "FactSalesQuota"]

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("data", "raw")
os.makedirs(out, exist_ok=True)
for f in FILES:
    dest = os.path.join(out, f + ".csv")
    if os.path.exists(dest):
        continue
    print("Tải", f)
    urllib.request.urlretrieve(BASE + f + ".csv", dest)
print("Xong ->", out)
