"""
Bank statements: a reader of their own.

A statement is not an invoice. Its rows begin at a date, its amounts are debits
and credits, and the balance printed beside each row says whether the row was
read correctly. The general reader in `reader.columns` has to guess at all of
that from the shape of a table; this one is told, because the user chose
"bank statement" rather than the reader working it out.

It shares the page's words, the recogniser and the output shape with the
general reader, so the viewer, the edits and the export need nothing new.
"""
