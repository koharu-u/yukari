#include <stdarg.h>
#include <stdio.h>

int ft_printf(const char *format, ...)
{
    static int calls;
    va_list arguments;
    int result;

    ++calls;
    if (calls == 3)
        return printf("LEAK");
    va_start(arguments, format);
    result = vprintf(format, arguments);
    va_end(arguments);
    return result;
}
