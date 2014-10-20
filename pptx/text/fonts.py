# encoding: utf-8

"""
Objects related to system font file lookup.
"""

from __future__ import absolute_import, print_function

import os
import sys

from struct import calcsize, unpack_from

from ..util import lazyproperty


class FontFiles(object):
    """
    A class-based singleton serving as a lazy cache for system font details.
    """

    _font_files = None

    @classmethod
    def find(cls, family_name, is_bold, is_italic):
        """
        Return the absolute path to the installed OpenType font having
        *family_name* and the styles *is_bold* and *is_italic*.
        """
        if cls._font_files is None:
            cls._font_files = cls._installed_fonts()
        return cls._font_files[(family_name, is_bold, is_italic)]

    @classmethod
    def _installed_fonts(cls):
        """
        Return a dict mapping a font descriptor to its font file path,
        containing all the font files resident on the current machine. The
        font descriptor is a (family_name, is_bold, is_italic) 3-tuple.
        """
        fonts = {}
        for d in cls._font_directories():
            for key, path in cls._iter_font_files_in(d):
                fonts[key] = path
        return fonts

    @classmethod
    def _font_directories(cls):
        """
        Return a sequence of directory paths likely to contain fonts on the
        current platform.
        """
        if sys.platform.startswith('darwin'):
            return cls._os_x_font_directories()
        if sys.platform.startswith('win32'):
            return cls._windows_font_directories()
        raise OSError('unsupported operating system')

    @classmethod
    def _iter_font_files_in(cls, directory):
        """
        Generate the OpenType font files found in and under *directory*. Each
        item is a key/value pair. The key is a (family_name, is_bold,
        is_italic) 3-tuple, like ('Arial', True, False), and the value is the
        absolute path to the font file.
        """
        for root, dirs, files in os.walk(directory):
            for filename in files:
                file_ext = os.path.splitext(filename)[1]
                if file_ext.lower() not in ('.otf', '.ttf'):
                    continue
                path = os.path.abspath(os.path.join(root, filename))
                with _Font.open(path) as f:
                    yield ((f.family_name, f.is_bold, f.is_italic), path)

    @classmethod
    def _os_x_font_directories(cls):
        """
        Return a sequence of directory paths on a Mac in which fonts are
        likely to be located.
        """
        os_x_font_dirs = [
            '/Library/Fonts',
            '/Network/Library/Fonts',
            '/System/Library/Fonts',
        ]
        home = os.environ.get('HOME')
        if home is not None:
            os_x_font_dirs.extend([
                os.path.join(home, 'Library', 'Fonts'),
                os.path.join(home, '.fonts')
            ])
        return os_x_font_dirs

    @classmethod
    def _windows_font_directories(cls):
        """
        Return a sequence of directory paths on Windows in which fonts are
        likely to be located.
        """
        raise NotImplementedError


class _Font(object):
    """
    A wrapper around an OTF/TTF font file stream that knows how to parse it
    for its name and style characteristics, e.g. bold and italic.
    """
    def __init__(self, stream):
        self._stream = stream

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, exception_tb):
        self._stream.close()

    @classmethod
    def open(cls, font_file_path):
        """
        Return a |_Font| instance loaded from *font_file_path*.
        """
        return cls(_Stream.open(font_file_path))

    @property
    def family_name(self):
        """
        The name of the typeface family for this font, e.g. 'Arial'. The full
        typeface name includes optional style names, such as 'Regular' or
        'Bold Italic'. This attribute is only the common base name shared by
        all fonts in the family.
        """
        return self._tables['name'].family_name

    @lazyproperty
    def _fields(self):
        """
        A 5-tuple containing the fields read from the font file header, also
        known as the offset table.
        """
        # sfnt_version, tbl_count, search_range, entry_selector, range_shift
        return self._stream.read_fields('>4sHHHH', 0)

    def _iter_table_records(self):
        """
        Generate a (tag, offset, length) 3-tuple for each of the tables in
        this font file.
        """
        count = self._table_count
        bufr = self._stream.read(offset=12, length=count*16)
        tmpl = '>4sLLL'
        for i in range(count):
            offset = i * 16
            tag, checksum, off, len_ = unpack_from(tmpl, bufr, offset)
            yield tag, off, len_

    @lazyproperty
    def _tables(self):
        """
        A mapping of OpenType table tag, e.g. 'name', to a table object
        providing access to the contents of that table.
        """
        return dict(
            (tag, _TableFactory(tag, self._stream, off, len_))
            for tag, off, len_ in self._iter_table_records()
        )

    @property
    def _table_count(self):
        """
        The number of tables in this OpenType font file.
        """
        return self._fields[1]


class _Stream(object):
    """
    A thin wrapper around a file that facilitates reading C-struct values
    from a binary file.
    """
    def __init__(self, file):
        self._file = file

    @classmethod
    def open(cls, path):
        """
        Return a |_Stream| providing binary access to the contents of the
        file at *path*.
        """
        return cls(open(path, 'rb'))

    def close(self):
        """
        Close the wrapped file. Using the stream after closing raises an
        exception.
        """
        self._file.close()

    def read(self, offset, length):
        """
        Return *length* bytes from this stream starting at *offset*.
        """
        self._file.seek(offset)
        return self._file.read(length)

    def read_fields(self, template, offset=0):
        """
        Return a tuple containing the C-struct fields in this stream
        specified by *template* and starting at *offset*.
        """
        self._file.seek(offset)
        bufr = self._file.read(calcsize(template))
        return unpack_from(template, bufr)


class _BaseTable(object):
    """
    Base class for OpenType font file table objects.
    """
    def __init__(self, tag, stream, offset, length):
        self._tag = tag
        self._stream = stream
        self._offset = offset
        self._length = length


class _HeadTable(_BaseTable):
    """
    OpenType font table having the tag 'head' and containing certain header
    information for the font, including its bold and/or italic style.
    """
    def __init__(self, stream, offset, length):
        super(_HeadTable, self).__init__('head', stream, offset, length)


class _NameTable(_BaseTable):
    """
    An OpenType font table having the tag 'name' and containing the
    name-related strings for the font.
    """
    def __init__(self, stream, offset, length):
        super(_NameTable, self).__init__('name', stream, offset, length)

    @property
    def family_name(self):
        """
        The name of the typeface family for this font, e.g. 'Arial'.
        """
        raise NotImplementedError


def _TableFactory(tag, font_file, offset, length):
    """
    Return an instance of |Table| appropriate to *tag*, loaded from
    *font_file* with content of *length* starting at *offset*.
    """
    raise NotImplementedError
