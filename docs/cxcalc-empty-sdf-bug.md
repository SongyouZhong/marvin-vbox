# Bug Report: cxcalc 计算结果异常（空 SDF 导致属性全为 0）

## 问题现象

前端 `QRTest2` 项目中的化合物，在 MARV（ChemAxon）计算列中只显示以下四个属性，其余属性均为空：

- 酸性 pKa(2)
- 碱性 pKa(1)
- 碱性 pKa(2)
- PSA(MARV)

且所有显示出来的值均为 **0**，与真实分子结构无关。

## 根本原因

`CxCalcWorker._build_sdf()` 生成的 SDF 文件包含 **0 个原子、0 条键** 的空 molblock：

```
CC(=O)Oc1ccccc1C(=O)O     ← 分子名称行（SMILES）
     RDKit          3D

  0  0  0  0  0  0  0  0  0  0999 V2000   ← 0 原子！
M  END
> <SMILES>
CC(=O)Oc1ccccc1C(=O)O

$$$$
```

cxcalc 对空分子结构的处理行为：

| 属性 | 返回值 | 原因 |
|------|--------|------|
| `psa` | 0.00 | 无极性基团 |
| `apKa2`, `bpKa1`, `bpKa2` | 0 | 无可电离基团时的默认值 |
| `logP`, `fsp3`, `dipole`, `HBD`, `HBA`, `logS`, `logD` 等 | 空（NULL） | 无原子无法计算 |

由于 `parse_merged_tsv()` 在解析时发现上述几个字段有值（0），未触发 `failed` 状态，而是写入了 `status='success'`，导致 **490 条错误记录静默通过**。

## 影响范围

- **项目**：QRTest2（project_id: `241f3fe4-cb82-482a-aba4-b50dcfe57530`）
- **异常记录数**：490 条（`status='success'` 但 `logp IS NULL`）
- **写入时间**：2026-04-21 03:04–03:05
- **其他项目**：`cx_compute_result` 中所有 `status='success' AND logp IS NULL` 的记录均受影响

## 修复方案

### 核心修复：使用 RDKit 生成真实 SDF

**文件**：`app/worker/cxcalc_worker.py`

使用 RDKit 的 `MolFromSmiles` + `Compute2DCoords` + `SDWriter` 生成含真实原子坐标的 SDF，替代原来手写的 0 原子占位 molblock。分子名称行（`_Name`）仍设为 SMILES 字符串，与 `cxcalc -i Name` 和结果处理器的匹配逻辑兼容。

```python
# 修复前（错误）
  0  0  0  0  0  0  0  0  0  0999 V2000   # 0 原子

# 修复后（正确）
from rdkit import Chem
from rdkit.Chem import AllChem

mol = Chem.MolFromSmiles(smiles)
mol.SetProp("_Name", smiles)
AllChem.Compute2DCoords(mol)
writer.write(mol)
```

**文件**：`requirements.txt`

```
rdkit>=2023.3.1
```

### 数据修复

已将 QRTest2 中 490 条异常记录由 `success` 重置为 `failed`：

```sql
UPDATE cx_compute_result
SET status = 'failed',
    msg = '计算结果异常：输入为空SDF(0原子)，已重置待重新计算',
    updated_at = NOW()
WHERE project_id = '241f3fe4-cb82-482a-aba4-b50dcfe57530'
  AND status = 'success'
  AND logp IS NULL;
-- 影响行数：490
```

`CxCalcSyncChecker` 每 60 秒扫描一次，将自动将 `failed` 记录重新提交计算。

## 部署步骤

重新构建并部署 marvin-vbox Docker 镜像：

```bash
cd /home/songyou/projects/marvin-vbox
docker build -t harbor.createrna.com/apps/marvin-vbox:latest .
docker push harbor.createrna.com/apps/marvin-vbox:latest
# 重启容器（或通过 docker-compose）
```

## 验证方法

修复部署后，等待下一轮 cxcalc 同步扫描完成，检查数据库：

```sql
-- 验证 QRTest2 的记录是否已有正常值
SELECT
    COUNT(*) FILTER (WHERE logp IS NOT NULL)       AS has_logp,
    COUNT(*) FILTER (WHERE psa IS NOT NULL)        AS has_psa,
    COUNT(*) FILTER (WHERE fsp3 IS NOT NULL)       AS has_fsp3,
    COUNT(*) FILTER (WHERE status = 'success')     AS success_count,
    COUNT(*)                                       AS total
FROM cx_compute_result
WHERE project_id = '241f3fe4-cb82-482a-aba4-b50dcfe57530';
```

预期结果：`has_logp` = `success_count` = `total`（所有成功记录均有 logP 值）。
