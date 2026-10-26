"""Expose CI test failures as check annotations, including native Windows COM errors."""

import sys
import xml.etree.ElementTree as ET


def escape(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


for case in ET.parse(sys.argv[1]).iter("testcase"):
    for tag in ("failure", "error"):
        failure = case.find(tag)
        if failure is not None:
            message = failure.get("message") or failure.text or "Test failed"
            title = (case.get("classname", "") + "." + case.get("name", ""))[:200]
            print("::error title=" + escape(title) + "::" + escape(message[:6000]))
