"""Kaggle-side trainer for a bughunter forge corpus.

Reads:
- /kaggle/input/<corpus-dataset>/train.jsonl
- /kaggle/input/<corpus-dataset>/valid.jsonl   (optional)
- /kaggle/input/<corpus-dataset>/preferences.jsonl   (optional DPO)
- /kaggle/working/config.json  (shipped by kaggle_finetune.push)

Writes:
- /kaggle/working/adapter/  — safetensors + adapter_config.json
- /kaggle/working/adapter_<run_id>.zip  — archive for pull-adapter
- pushes the zip into the configured output dataset (kaggle datasets version)

Expected Kaggle env: free T4 x2 or P100. Deps installed inline because
Kaggle's Internet toggle is on.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


WORK = Path("/kaggle/working")
INPUT = Path("/kaggle/input")


def pip(*args):
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "--quiet", "--disable-pip-version-check", *args])


def install_deps(use_unsloth: bool) -> None:
    # tiny, fast QLoRA path. Unsloth gives 2x speed on T4.
    pip("--upgrade", "pip")
    pip("torch", "--index-url", "https://download.pytorch.org/whl/cu121")
    if use_unsloth:
        pip("unsloth[kaggle]", "trl", "peft", "transformers>=4.44",
            "accelerate", "bitsandbytes", "datasets")
    else:
        pip("trl", "peft", "transformers>=4.44", "accelerate",
            "bitsandbytes", "datasets")


def load_config() -> dict:
    cfg_path = WORK / "config.json"
    if not cfg_path.exists():
        # when the notebook push places config.json alongside the script
        cfg_path = Path(__file__).parent / "config.json"
    with cfg_path.open() as f:
        return json.load(f)


def find_corpus(dataset_slug: str) -> Path:
    # Kaggle unpacks dataset_sources into /kaggle/input/<dataset-name>/
    name = dataset_slug.split("/", 1)[-1]
    direct = INPUT / name
    if direct.exists():
        return direct
    # fallback: first subdirectory that contains train.jsonl
    for sub in INPUT.iterdir():
        if (sub / "train.jsonl").exists():
            return sub
    raise RuntimeError("corpus dataset not found under /kaggle/input")


def to_chatml(messages: list[dict]) -> str:
    out = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        out.append(f"<|im_start|>{role}\n{content}<|im_end|>")
    return "\n".join(out)


def build_dataset(corpus: Path, tokenizer, seq_len: int):
    from datasets import Dataset
    rows = []
    with (corpus / "train.jsonl").open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                messages = record.get("messages") or []
                if not messages:
                    continue
                text = tokenizer.apply_chat_template(
                    messages, tokenize=False,
                    add_generation_prompt=False,
                ) if hasattr(tokenizer, "apply_chat_template") else to_chatml(messages)
                rows.append({"text": text, "weight": float(record.get("weight", 1.0))})
            except Exception:
                continue
    train = Dataset.from_list(rows)
    valid_path = corpus / "valid.jsonl"
    if valid_path.exists():
        vrows = []
        with valid_path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    messages = record.get("messages") or []
                    if not messages:
                        continue
                    text = tokenizer.apply_chat_template(
                        messages, tokenize=False,
                        add_generation_prompt=False,
                    ) if hasattr(tokenizer, "apply_chat_template") else to_chatml(messages)
                    vrows.append({"text": text})
                except Exception:
                    continue
        valid = Dataset.from_list(vrows) if vrows else None
    else:
        valid = None
    return train, valid


def train_lora(cfg: dict, corpus: Path) -> Path:
    from transformers import AutoTokenizer, AutoModelForCausalLM, TrainingArguments
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from trl import SFTTrainer
    import torch

    base_model = cfg["base_model"]
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        load_in_4bit=True,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=cfg.get("rank", 16),
        lora_alpha=cfg.get("alpha", 32),
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=cfg.get("target_modules",
                               ["q_proj", "k_proj", "v_proj", "o_proj",
                                "gate_proj", "up_proj", "down_proj"]),
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    train_ds, valid_ds = build_dataset(corpus, tokenizer, cfg.get("seq_len", 4096))

    out_dir = WORK / "adapter"
    args = TrainingArguments(
        output_dir=str(out_dir),
        per_device_train_batch_size=cfg.get("batch_size", 4),
        gradient_accumulation_steps=cfg.get("grad_accum", 4),
        num_train_epochs=cfg.get("epochs", 1),
        learning_rate=cfg.get("lr", 2e-4),
        bf16=True,
        logging_steps=25,
        save_strategy="epoch",
        evaluation_strategy="no" if valid_ds is None else "epoch",
        warmup_ratio=0.03,
        lr_scheduler_type="cosine",
        report_to="none",
        save_total_limit=1,
        gradient_checkpointing=True,
    )
    trainer = SFTTrainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=valid_ds,
        tokenizer=tokenizer,
        max_seq_length=cfg.get("seq_len", 4096),
        dataset_text_field="text",
        packing=True,
    )
    trainer.train()
    trainer.model.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)
    return out_dir


def maybe_dpo(cfg: dict, corpus: Path, adapter_dir: Path) -> Path:
    """Optional DPO pass on preferences.jsonl."""
    pref_path = corpus / "preferences.jsonl"
    if not pref_path.exists():
        return adapter_dir
    try:
        from transformers import AutoTokenizer, AutoModelForCausalLM
        from peft import PeftModel
        from trl import DPOTrainer, DPOConfig
        from datasets import Dataset
        import torch
    except Exception as e:
        print(f"DPO deps missing, skipping: {e}")
        return adapter_dir
    base_model = cfg["base_model"]
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        base_model, load_in_4bit=True, device_map="auto",
        torch_dtype=torch.bfloat16, trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(model, adapter_dir, is_trainable=True)
    rows = []
    with pref_path.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                rows.append({"prompt": r["prompt"],
                             "chosen": r["chosen"],
                             "rejected": r["rejected"]})
            except Exception:
                continue
    if not rows:
        return adapter_dir
    ds = Dataset.from_list(rows)
    dpo_out = WORK / "adapter_dpo"
    args = DPOConfig(output_dir=str(dpo_out), per_device_train_batch_size=2,
                     gradient_accumulation_steps=4, num_train_epochs=1,
                     learning_rate=5e-6, bf16=True, logging_steps=25,
                     report_to="none")
    trainer = DPOTrainer(model=model, args=args, train_dataset=ds,
                         tokenizer=tokenizer, max_length=cfg.get("seq_len", 4096))
    trainer.train()
    trainer.model.save_pretrained(dpo_out)
    tokenizer.save_pretrained(dpo_out)
    return dpo_out


def package_output(adapter_dir: Path, cfg: dict) -> Path:
    run_id = cfg.get("run_id", "run")
    archive = WORK / f"adapter_{run_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in adapter_dir.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(adapter_dir))
    manifest = {"run_id": run_id, "base_model": cfg["base_model"],
                "corpus": cfg["corpus_dataset"],
                "rank": cfg.get("rank"), "alpha": cfg.get("alpha")}
    (WORK / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return archive


def push_output_dataset(cfg: dict) -> None:
    """Update the operator's output dataset with the new adapter zip."""
    import subprocess
    out_slug = cfg["output_dataset"]
    # dataset-metadata.json in WORK referencing this slug
    (WORK / "dataset-metadata.json").write_text(json.dumps({
        "title": f"bughunter adapters",
        "id": out_slug,
        "licenses": [{"name": "CC0-1.0"}],
    }, indent=2))
    try:
        subprocess.check_call([sys.executable, "-m", "kaggle",
                               "datasets", "version",
                               "-p", str(WORK),
                               "-m", f"adapter {cfg.get('run_id','')}",
                               "--dir-mode", "zip"])
    except Exception as e:
        print(f"could not push output dataset: {e}")


def main() -> None:
    cfg = load_config()
    print(f"[forge] training config: {cfg}")
    install_deps(use_unsloth=cfg.get("use_unsloth", False))
    corpus = find_corpus(cfg["corpus_dataset"])
    adapter_dir = train_lora(cfg, corpus)
    adapter_dir = maybe_dpo(cfg, corpus, adapter_dir)
    archive = package_output(adapter_dir, cfg)
    print(f"[forge] adapter packaged at {archive}")
    push_output_dataset(cfg)
    print("[forge] done")


if __name__ == "__main__":
    main()
