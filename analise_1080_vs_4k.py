"""Compara a inspecao em 1080p e em 4K com as capturas de teste_1080_vs_4k.py.

A placa 1 de cada resolucao vira a golden. As ROIs de rois/Testes.json (desenhadas
na golden de 14/10, em 1920x1080) sao levadas para a nova golden pela homografia
entre as duas imagens (e multiplicadas por 2 no 4K). Para cada placa, mostra a
pontuacao de cada ROI nas duas resolucoes e a distancia ate o limiar.
Uso: python analise_1080_vs_4k.py
"""
import glob
import json
import os
import re
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pcb_inspector as m  # noqa: E402

DIR = os.path.join(HERE, "00. teste 1080 vs 4k")
GT = {1: set(), 2: set(), 15: {"R2"}, 16: {"R1"}, 25: set(), 37: {"LED2", "R1", "R2"}, 3: {"R3"}, 8: {"R8"},
      35: set(), 30: {"R1", "R5", "U2"}, 6: {"U2"}, 7: {"R5"}}  # placas usadas com mais frequencia neste teste


def board_number(label):
    return int(re.match(r"\d+", label).group())


def map_rois(rois, src_golden, dst_golden):
    """Move the ROI rectangles from src_golden to dst_golden coordinates (same resolution)."""
    _, _, info = m.align_image_info(dst_golden, src_golden)
    H = info["homography"]
    out = []
    for r in rois:
        pts = np.float32([[r["x"], r["y"]], [r["x"] + r["w"], r["y"] + r["h"]]]).reshape(-1, 1, 2)
        (x0, y0), (x1, y1) = cv2.perspectiveTransform(pts, H).reshape(-1, 2)
        out.append(dict(r, x=int(round(x0)), y=int(round(y0)), w=int(round(x1 - x0)), h=int(round(y1 - y0))))
    return out


def main():
    rois_orig = json.load(open(os.path.join(HERE, "rois", "Testes.json"), encoding="utf-8"))["regions"]
    old_golden = cv2.imread(os.path.join(HERE, "Golden Boards Images", "Testes.png"))
    files = {k: sorted(glob.glob(os.path.join(DIR, f"placa*_{k}.png"))) for k in ("1080", "4k")}
    labels = [re.match(r"placa(.+)_1080\.png", os.path.basename(f)).group(1) for f in files["1080"]]
    if "1" not in labels:
        print("Falta a placa 1 (golden)."); return
    gold = {k: cv2.imread(os.path.join(DIR, f"placa1_{k}.png")) for k in ("1080", "4k")}
    rois1080 = map_rois(rois_orig, old_golden, gold["1080"])
    rois = {"1080": rois1080,
            "4k": [dict(r, x=2 * r["x"], y=2 * r["y"], w=2 * r["w"], h=2 * r["h"]) for r in rois1080]}
    thr = {"component": m.ROI_DIFF_RATIO_THRESHOLD, "hole": m.HOLE_CHROMA_THRESHOLD, "metal": m.METAL_EDGE_RECALL_MIN}
    results = {}
    for label in labels:
        if label == "1":
            continue
        for k in ("1080", "4k"):
            f = os.path.join(DIR, f"placa{label}_{k}.png")
            if not os.path.isfile(f):
                continue
            al, vm, info = m.align_image_info(gold[k], cv2.imread(f))
            placement = m.check_placement(gold[k], al.shape, info, rois[k])
            res = {r["name"]: r for r in m.evaluate_rois(gold[k], al, vm, rois[k])}
            results[(label, k)] = (placement, res)

    print("Pontuacao de cada componente (limiar 0,30). '*' = NOK. Ausente esperado marcado com [A].\n")
    comp = [r["name"] for r in rois_orig if r.get("kind", "component") == "component"]
    print(f"{'placa':7s} {'res':5s} " + " ".join(f"{n:>6s}" for n in comp))
    for label in labels:
        if label == "1":
            continue
        exp = GT.get(board_number(label))
        for k in ("1080", "4k"):
            if (label, k) not in results:
                continue
            placement, res = results[(label, k)]
            if placement:
                print(f"{label:7s} {k:5s} descartada: {placement}"); continue
            cells = []
            for n in comp:
                s = res[n]["score"]
                tag = "*" if res[n]["status"] == "NOK" else " "
                miss = "A" if exp and n in exp else " "
                cells.append(f"{s:5.2f}{tag}{miss}" if s is not None else "   -  ")
            print(f"{label:7s} {k:5s} " + " ".join(cells))
        if exp is None:
            print(f"        (placa {board_number(label)} sem gabarito neste script)")

    print("\nResumo: menor pontuacao entre componentes AUSENTES e maior entre PRESENTES, por resolucao")
    for k in ("1080", "4k"):
        miss, pres = [], []
        for label in labels:
            if label == "1" or (label, k) not in results or results[(label, k)][0]:
                continue
            exp = GT.get(board_number(label))
            if exp is None:
                continue
            for n in comp:
                s = results[(label, k)][1][n]["score"]
                (miss if n in exp else pres).append((s, n, label))
        if miss and pres:
            lo, hi = min(miss), max(pres)
            print(f"  {k:5s}: ausente mais baixo {lo[0]:.3f} ({lo[1]}, placa {lo[2]}) | presente mais alto {hi[0]:.3f} ({hi[1]}, placa {hi[2]})"
                  f" | folga ate 0,30: ausentes {lo[0] - thr['component']:+.3f}, presentes {thr['component'] - hi[0]:+.3f}")
    print("\nObs.: o limiar 0,30 foi definido em 1080p; no 4K e so referencia.")


if __name__ == "__main__":
    main()
