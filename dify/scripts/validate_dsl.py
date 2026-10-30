"""Run inside the pinned official Dify API image to validate real node contracts.

Example: docker compose ... exec -T api python /tmp/validate_dsl.py /tmp/app.yml
This verifies typed node data and variable references, not installation or runtime.
"""
import importlib
import json
import sys

import yaml

MODELS = {
    "start": ("graphon.nodes.start.entities", "StartNodeData"),
    "code": ("graphon.nodes.code.entities", "CodeNodeData"),
    "http-request": ("graphon.nodes.http_request.entities", "HttpRequestNodeData"),
    "if-else": ("graphon.nodes.if_else.entities", "IfElseNodeData"),
    "variable-aggregator": ("graphon.nodes.variable_aggregator.entities", "VariableAggregatorNodeData"),
    "llm": ("graphon.nodes.llm.entities", "LLMNodeData"),
    "answer": ("graphon.nodes.answer.entities", "AnswerNodeData"),
}


def main():
    data = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
    assert data["version"] == "0.7.0"
    nodes = data["workflow"]["graph"]["nodes"]
    ids = {node["id"] for node in nodes}
    checked = []
    for node in nodes:
        module, name = MODELS[node["data"]["type"]]
        cls = getattr(importlib.import_module(module), name)
        cls.model_validate(node["data"])
        checked.append(node["id"])
    for edge in data["workflow"]["graph"]["edges"]:
        assert edge["source"] in ids and edge["target"] in ids
    print(json.dumps({"dify": "1.17.1", "graphon": "0.7.0", "node_contracts_validated": checked}, ensure_ascii=False))


if __name__ == "__main__":
    main()
