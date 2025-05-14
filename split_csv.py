# split_csv.py

import os
import csv
import argparse

def split_csv(input_path, output_dir, prefix, chunk_size):
    os.makedirs(output_dir, exist_ok=True)
    with open(input_path, 'r', newline='', encoding='utf-8') as infile:
        reader = csv.reader(infile)
        header = next(reader)

        file_idx = 1
        row_count = 0
        outfile = None
        writer = None

        def open_new_file(idx):
            filename = f"{prefix}_{idx:03d}.csv"
            path = os.path.join(output_dir, filename)
            f = open(path, 'w', newline='', encoding='utf-8')
            w = csv.writer(f)
            w.writerow(header)
            print(f"→ Tạo file: {path}")
            return f, w

        # Khởi tạo file đầu tiên
        outfile, writer = open_new_file(file_idx)

        for row in reader:
            if row_count >= chunk_size:
                outfile.close()
                file_idx += 1
                row_count = 0
                outfile, writer = open_new_file(file_idx)
            writer.writerow(row)
            row_count += 1

        if outfile:
            outfile.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Split a large CSV into multiple smaller CSV files."
    )
    parser.add_argument(
        '--input', '-i', required=True,
        help="Đường dẫn đến file CSV gốc"
    )
    parser.add_argument(
        '--output-dir', '-o', default='chunks',
        help="Thư mục chứa các file con (sẽ tự tạo nếu chưa có)"
    )
    parser.add_argument(
        '--prefix', '-p', default='part',
        help="Tiền tố tên file con"
    )
    parser.add_argument(
        '--chunk-size', '-c', type=int, default=1000000,
        help="Số dòng tối đa trong mỗi file con (mặc định 1_000_000)"
    )
    args = parser.parse_args()

    split_csv(
        input_path=args.input,
        output_dir=args.output_dir,
        prefix=args.prefix,
        chunk_size=args.chunk_size
    )
