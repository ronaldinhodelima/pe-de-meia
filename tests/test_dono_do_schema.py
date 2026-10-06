"""O Node e o dono do schema desde 06/10/2026 (CLAUDE.md §3).

As migracoes 1 a 70 continuam aqui, porque um banco novo precisa delas e reescrever
migracao aplicada criaria divergencia de schema. Toda mudanca nova entra no Node
(`pe-de-meia-node/apps/api/src/migracoes`), com numero a partir de 71. Duas listas
de migracao vivas acabariam usando o mesmo numero para coisas diferentes.
"""
import re
from pathlib import Path

ULTIMA_DO_FLASK = 70


def test_nenhuma_migracao_nova_no_flask():
    texto = (Path(__file__).resolve().parent.parent / "core.py").read_text(encoding="utf-8")
    versoes = [int(v) for v in re.findall(r"if versao_atual < (\d+):", texto)]
    assert versoes, "nao achei os blocos de migracao do core.py"
    assert max(versoes) == ULTIMA_DO_FLASK, (
        "migracao nova no core.py: desde a 71 elas moram no Node (apps/api/src/migracoes)"
    )
