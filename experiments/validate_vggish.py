"""Compare ohrin.vggish output with OpenMIC's stored embeddings on extracted clips."""
import argparse
import json
from pathlib import Path

import numpy as np

from ohrin.vggish import Extractor, decode_mono_16k


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-root', default='data/openmic-2018')
    parser.add_argument('--weights', default='data/vggish')
    parser.add_argument('--out', default='outputs/vggish-validation.json')
    args = parser.parse_args()
    root = Path(args.data_root)
    with np.load(root / 'openmic-2018.npz', allow_pickle=True) as data:
        keys = data['sample_key'].astype(str)
        stored = data['X']
    index = {k: i for i, k in enumerate(keys)}
    extractor = Extractor(args.weights)
    rows = []
    for path in sorted((root / 'audio_subset').rglob('*.ogg')):
        key = path.stem
        ref = stored[index[key]].astype(np.float64)
        got = extractor.embed(decode_mono_16k(path)).astype(np.float64)
        n = min(len(ref), len(got))
        diff = np.abs(ref[:n] - got[:n])
        mean_ref, mean_got = ref[:n].mean(0), got[:n].mean(0)
        rows.append({
            'key': key, 'frames_ref': len(ref), 'frames_got': len(got),
            'mean_abs_diff': float(diff.mean()), 'max_abs_diff': float(diff.max()),
            'exact_frac': float((diff == 0).mean()),
            'within_2_frac': float((diff <= 2).mean()),
            'mean_vector_corr': float(np.corrcoef(mean_ref, mean_got)[0, 1]),
            'ref_std_across_dims': float(ref.std()),
        })
    report = {'clips': rows, 'n': len(rows)}
    Path(args.out).parent.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2))
    for r in rows:
        print(r['key'], f"mad={r['mean_abs_diff']:.2f} max={r['max_abs_diff']:.0f} <=2:{r['within_2_frac']:.2f} corr={r['mean_vector_corr']:.3f}")


if __name__ == '__main__':
    main()
