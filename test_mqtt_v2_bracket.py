import msgpack
import gzip
import io

def test_bracket_format():
    tag_mac = "8200005F320B"
    image_bytes = b"FAKE_BMP_IMAGE_DATA_FOR_TESTING"
    token = 211

    # Gzip image data
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as f:
        f.write(image_bytes)
    gzipped_image = buf.getvalue()

    # The ESLEntity2 array
    esl_entity = [
        tag_mac,        # 0: TagID
        0,              # 1: Pattern
        0,              # 2: PageIndex
        False,          # 3: R
        True,           # 4: G
        False,          # 5: B
        60,             # 6: Times
        token,          # 7: Token
        "",             # 8: OldKey
        "",             # 9: NewKey
        gzipped_image,  # 10: Bytes
        True            # 11: Compress
    ]

    # PayloadSegment: An array of ESLEntity2 objects
    payload_segment = [esl_entity]

    # Serialize with MessagePack
    packed_payload = msgpack.packb(payload_segment)

    # Unpack and verify structure
    unpacked = msgpack.unpackb(packed_payload)

    print(f"Unpacked Payload (Top Level): {type(unpacked)}")
    print(f"Payload contains {len(unpacked)} item(s)")

    first_item = unpacked[0]
    print(f"First Item Type: {type(first_item)}")
    print(f"First Item Length: {len(first_item)}")

    if isinstance(first_item, list) and len(first_item) == 12:
        print("\nStructure matches Protocol 2.0: [[...]] (List of entities)")
    else:
        print("\nStructure mismatch!")
        exit(1)

if __name__ == "__main__":
    test_bracket_format()
