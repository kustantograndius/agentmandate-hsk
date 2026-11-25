# Bukti Uji End-to-End Lokal — AgentMandate (Fase 2, 22 Nov 2026)

Uji penuh alur pembayaran HSP/AgentMandate di chain lokal (anvil,
chainId 31337) dengan MockUSDC — tanpa jaringan eksternal, tanpa dana
nyata. Driver: `e2e/run_e2e.py` (jalankan ulang kapan pun:
`python3 e2e/run_e2e.py`). Log mentah lengkap:
[`e2e-local-20261122.log`](e2e-local-20261122.log).

## Hasil: PASS (semua tahap)

| Tahap | Bukti |
| --- | --- |
| Unit test kontrak | `forge test`: **12 passed; 0 failed** |
| Deploy lokal | MockUSDC `0x5FbDB2315678afecb367f032d93F642f64180aa3`, Registry `0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512`, A2ASettlement `0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0` |
| Penemuan layanan | Penjual mendaftarkan layanan #1 "HSK Wallet Report" (harga 10.000 atomik) di registry; agent pembeli membacanya kembali on-chain sebagai cross-check |
| Tantangan x402 | GET tanpa bayar → **HTTP 402**, skema `agent-mandate`, berisi payTo/amount/token/kontrak settlement/taskRef khusus permintaan itu |
| Mandat dibuat | Mandat #1 oleh principal: perTxCap 10.000, totalCap 100.000, expiry +60 menit (tx `0xe6816edf…d929f`); principal approve settlement sebesar totalCap |
| Otorisasi EIP-712 | Agent menandatangani `PaymentAuthorization` (mandateId, payee, amount, taskRef, nonce acak, deadline) |
| Settlement on-chain | `settle()` sukses (tx `0x73d337f7…5200`) → struk #1 tertulis |
| Struk terverifikasi | `receipts(1)` = payer principal, payee penjual, amount 10.000, taskRef cocok dengan tantangan 402 |
| Deliverable terkirim | Retry dengan header `X-Payment-Receipt: 1` → **HTTP 200**, laporan snapshot wallet (saldo native + token + tx count alamat agent) + blok `paidVia` berisi struk |
| Akuntansi | Saldo mUSDC penjual = **10.000** persis; `mandates(1).spent` = **10.000** |
| Negatif: replay | Struk #1 dipakai ulang → **402 ditolak** (sudah ditebus) |
| Negatif: struk palsu | `X-Payment-Receipt: 999` → **402 ditolak** |
| Negatif: tanpa bayar | GET polos → **402** |

Peran memakai akun dev publik anvil yang memang tidak aman (khusus lokal):
principal = akun 0, agent = akun 1, penjual = akun 2.

## Yang dibuktikan uji ini

Alur persis bentuk protokol HSP berjalan utuh di atas kontrak Fase 1:
mandat terbatas → tantangan HTTP 402 → tanda tangan EIP-712 per tugas →
settlement yang ditegakkan kontrak (cap ganda, anti-replay, hanya agent
mandat) → struk on-chain yang bisa diaudit → deliverable dilepas hanya
setelah struk diverifikasi server dari chain.
