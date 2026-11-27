#pragma once

#include <algorithm>
#include <cstddef>
#include <limits>

namespace buffers::abstract_buffer {
    /// @brief Макет пакета фиксированного размера.
    /// @tparam N Размер заголовка в байтах
    /// @tparam M Размер тела пакета в байтах
    template<std::size_t N, std::size_t M>
    class AbstractPacket {
        static_assert(N > 0, "N must be greater than 0");
        static_assert(M > 0, "M must be greater than 0");

    public:
        struct AbstractHeader {
            std::byte data[N]{std::byte(0)};
        };

        /// @brief Сохраняет заголовок в формируемый пакет
        /// и возвращает указатель на payload во входном буфере.
        /// @param rawPtr  Указатель на начало входного буфера.
        /// @param rawSize Количество доступных для чтения байт, начиная с @p rawPtr.
        /// @param header  Заголовок пакета (выходной параметр); при успехе
        ///                заполняется первыми @p N байтами буфера.
        /// @return Указатель на payload пакета при успехе, иначе @c nullptr.
        static const std::byte *stripHeader(const std::byte *rawPtr,
                                            std::size_t rawSize,
                                            AbstractHeader &header) {
            if (rawPtr == nullptr || rawSize < N + M) {
                return nullptr;
            }

            for (unsigned i = 0; i < N; i++) {
                header.data[i] = rawPtr[i];
            }

            return rawPtr + N;
        }
    };
} // namespace buffers::abstract_buffer
